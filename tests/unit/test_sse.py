"""Server-Sent Events endpoint (``GET /v1/events``).

The generator is exercised directly on a real event loop: Starlette's
``TestClient`` buffers a streaming response to completion before returning,
which would hang on an intentionally endless SSE stream. Driving the async
generator lets us assert delivery, filtering, heartbeats, and unsubscription
deterministically without sleeps or the network.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from tts_daemon.api import http
from tts_daemon.core.events import Event, EventBus


class _FakeRequest:
    """Minimal stand-in for a Starlette request the stream inspects."""

    def __init__(self, service: SimpleNamespace) -> None:
        self.app = SimpleNamespace(state=SimpleNamespace(service=service))
        self._disconnected = False

    def disconnect(self) -> None:
        self._disconnected = True

    async def is_disconnected(self) -> bool:
        return self._disconnected


def _service_with_bus() -> tuple[SimpleNamespace, EventBus]:
    bus = EventBus()
    return SimpleNamespace(events=bus), bus


def _parse_frame(frame: str) -> tuple[str, dict]:
    event_line, data_line, _ = frame.split("\n", 2)
    assert event_line.startswith("event: ")
    assert data_line.startswith("data: ")
    return event_line[len("event: ") :], json.loads(data_line[len("data: ") :])


def test_sse_frame_format() -> None:
    frame = http._sse_frame(Event("utterance.speaking", {"id": "x"}, timestamp=1.0))
    assert frame.startswith("event: utterance.speaking\ndata: ")
    assert frame.endswith("\n\n")
    frame_type, payload = _parse_frame(frame)
    assert frame_type == "utterance.speaking"
    assert payload == {"type": "utterance.speaking", "data": {"id": "x"}, "timestamp": 1.0}


def test_stream_prelude_delivers_and_unsubscribes() -> None:
    async def scenario() -> None:
        service, bus = _service_with_bus()
        request = _FakeRequest(service)
        gen = http._event_stream(request, service, None)

        prelude = await gen.__anext__()
        assert prelude.startswith(":")  # comment opens the stream
        assert bus.subscriber_count == 1

        bus.publish("utterance.finished", {"id": "abc"})
        frame_type, payload = _parse_frame(await gen.__anext__())
        assert frame_type == "utterance.finished"
        assert payload["data"] == {"id": "abc"}

        request.disconnect()
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()
        assert bus.subscriber_count == 0  # finally-block unsubscribed

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_stream_filters_by_type() -> None:
    async def scenario() -> None:
        service, bus = _service_with_bus()
        request = _FakeRequest(service)
        gen = http._event_stream(request, service, {"queue.cleared"})
        await gen.__anext__()  # prelude

        bus.publish("utterance.speaking", {"id": "ignored"})
        bus.publish("queue.cleared", {"cancelled": 3})
        frame_type, payload = _parse_frame(await gen.__anext__())
        assert frame_type == "queue.cleared"
        assert payload["data"] == {"cancelled": 3}
        await gen.aclose()

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_stream_emits_heartbeat_when_idle(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(http, "_SSE_HEARTBEAT_SECONDS", 0.02)

    async def scenario() -> None:
        service, _ = _service_with_bus()
        request = _FakeRequest(service)
        gen = http._event_stream(request, service, None)
        await gen.__anext__()  # prelude
        heartbeat = await gen.__anext__()  # nothing published -> a ping comment
        assert heartbeat.strip() == ": ping"
        await gen.aclose()

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))


def test_events_route_wires_streaming_response() -> None:
    async def scenario() -> None:
        service, _ = _service_with_bus()
        request = _FakeRequest(service)
        response = await http.events(request, types="a.b,c.d")
        assert response.media_type == "text/event-stream"
        assert response.headers["cache-control"] == "no-cache"
        await response.body_iterator.aclose()  # never started; just tidy up

    asyncio.run(asyncio.wait_for(scenario(), timeout=5))
