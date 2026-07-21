"""SSE endpoint tests.

The streaming route (`GET /v1/events`) is unbounded, and httpx's ASGI test
transport buffers a whole response before yielding a byte — so an infinite
stream cannot be consumed through ``TestClient``. Instead we drive the real
streaming generator (`sse_event_stream`) and the event bridge directly, which
exercises the actual production code paths (filtering, framing, heartbeat,
unsubscribe) without the harness limitation. A cheap registration check keeps
the wiring honest.
"""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from tests.conftest import make_config
from tts_daemon.api.app import create_app
from tts_daemon.api.event_bridge import offer, subscribe_event_queue
from tts_daemon.api.http import _format_sse, _parse_type_filter, sse_event_stream
from tts_daemon.core.events import Event, EventBus


def _run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=5.0))


class TestSseHelpers:
    def test_format_sse_frame(self) -> None:
        frame = _format_sse(Event("utterance.finished", {"id": "abc"}))
        lines = frame.split("\n")
        assert lines[0] == "event: utterance.finished"
        assert lines[1].startswith("data: ")
        assert frame.endswith("\n\n")
        payload = json.loads(lines[1][len("data: ") :])
        assert payload["type"] == "utterance.finished"
        assert payload["data"] == {"id": "abc"}

    def test_parse_type_filter(self) -> None:
        assert _parse_type_filter(None) is None
        assert _parse_type_filter("") is None
        assert _parse_type_filter("  ,  ") is None
        assert _parse_type_filter("a, b ,a") == frozenset({"a", "b"})


class TestSseStream:
    def test_opens_with_comment_then_streams_events(self) -> None:
        async def scenario() -> None:
            queue: asyncio.Queue[Event] = asyncio.Queue()
            stream = sse_event_stream(queue, None, heartbeat=0.05)
            assert await anext(stream) == ": connected\n\n"

            queue.put_nowait(Event("utterance.speaking", {"id": "x"}))
            frame = await anext(stream)
            assert frame.startswith("event: utterance.speaking\ndata: ")
            await stream.aclose()

        _run(scenario())

    def test_heartbeat_comment_when_idle(self) -> None:
        async def scenario() -> None:
            queue: asyncio.Queue[Event] = asyncio.Queue()
            stream = sse_event_stream(queue, None, heartbeat=0.01)
            assert await anext(stream) == ": connected\n\n"
            assert await anext(stream) == ": ping\n\n"  # nothing queued -> heartbeat
            await stream.aclose()

        _run(scenario())

    def test_type_filter_drops_unwanted_events(self) -> None:
        async def scenario() -> None:
            queue: asyncio.Queue[Event] = asyncio.Queue()
            stream = sse_event_stream(queue, frozenset({"queue.cleared"}), heartbeat=0.05)
            assert await anext(stream) == ": connected\n\n"

            queue.put_nowait(Event("utterance.finished", {"id": "ignored"}))
            queue.put_nowait(Event("queue.cleared", {"cancelled": 2}))
            frame = await anext(stream)
            assert frame.startswith("event: queue.cleared\n")
            await stream.aclose()

        _run(scenario())

    def test_on_close_runs_when_generator_closes(self) -> None:
        async def scenario() -> None:
            queue: asyncio.Queue[Event] = asyncio.Queue()
            closed: list[bool] = []
            stream = sse_event_stream(
                queue, None, on_close=lambda: closed.append(True), heartbeat=0.05
            )
            await anext(stream)
            await stream.aclose()
            assert closed == [True]

        _run(scenario())


class TestEventBridge:
    def test_offer_drops_oldest_when_full(self) -> None:
        queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=2)
        first, second, third = (Event(f"e{i}") for i in range(3))
        offer(queue, first)
        offer(queue, second)
        offer(queue, third)  # buffer full -> oldest (first) is dropped
        assert queue.get_nowait() is second
        assert queue.get_nowait() is third

    def test_subscribe_bridges_events_onto_loop(self) -> None:
        async def scenario() -> None:
            bus = EventBus()
            loop = asyncio.get_running_loop()
            queue, unsubscribe = subscribe_event_queue(bus, loop)
            try:
                bus.publish("utterance.queued", {"id": "y"})
                event = await asyncio.wait_for(queue.get(), timeout=1.0)
                assert event.type == "utterance.queued"
            finally:
                unsubscribe()
            assert bus.subscriber_count == 0

        _run(scenario())


def test_events_route_is_registered() -> None:
    app = create_app(make_config())
    with TestClient(app) as client:
        schema = client.get("/openapi.json").json()
    assert "/v1/events" in schema["paths"]
