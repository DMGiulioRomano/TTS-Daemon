"""WebSocket endpoint: commands in, results and live events out.

Protocol (JSON messages):

Client -> server::

    {"type": "speak", "text": "...", "provider"?, "voice"?, "speed"?,
     "options"?, "interrupt"?, "id"?}
    {"type": "stop", "id"?}
    {"type": "status", "id"?}
    {"type": "ping", "id"?}

``id`` is an optional client correlation value echoed back in the response.

Server -> client::

    {"type": "result", "id": ..., "request": "speak", "data": {...}}
    {"type": "error",  "id": ..., "detail": "..."}
    {"type": "event",  "event": {"type": "utterance.speaking", "data": {...},
                                 "timestamp": ...}}
    {"type": "pong",   "id": ...}

Every connection automatically receives all gateway events (utterance
lifecycle, queue clears). Events originate on the playback worker thread;
they are bridged into this connection's asyncio queue with
``call_soon_threadsafe``, and when a slow client falls behind the oldest
events are dropped rather than blocking playback.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from tts_gateway.api.schemas import SpeakRequest
from tts_gateway.core.errors import GatewayError
from tts_gateway.core.events import Event
from tts_gateway.core.service import SpeechService

logger = logging.getLogger(__name__)

router = APIRouter()

_EVENT_BUFFER = 256


def _offer(queue: asyncio.Queue[Event], event: Event) -> None:
    """Enqueue, dropping the oldest event when the buffer is full."""
    while True:
        try:
            queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()


@router.websocket("/v1/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    service: SpeechService = websocket.app.state.service
    await websocket.accept()

    loop = asyncio.get_running_loop()
    event_queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=_EVENT_BUFFER)

    def forward(event: Event) -> None:
        # Called on the playback worker thread; hop onto the loop. The loop
        # may already be closing when the server shuts down mid-event.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(_offer, event_queue, event)

    unsubscribe = service.events.subscribe(forward)
    sender = asyncio.create_task(_send_events(websocket, event_queue))
    try:
        while True:
            message = await websocket.receive_json()
            response = await _dispatch(service, message)
            await websocket.send_json(response)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("WebSocket connection failed")
    finally:
        unsubscribe()
        sender.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sender


async def _send_events(websocket: WebSocket, queue: asyncio.Queue[Event]) -> None:
    while True:
        event = await queue.get()
        await websocket.send_json({"type": "event", "event": event.to_dict()})


async def _dispatch(service: SpeechService, message: Any) -> dict[str, Any]:
    if not isinstance(message, dict):
        return {"type": "error", "id": None, "detail": "Message must be a JSON object"}
    correlation = message.get("id")
    command = message.get("type")
    try:
        if command == "speak":
            return await _handle_speak(service, message, correlation)
        if command == "stop":
            cancelled = await asyncio.to_thread(service.stop)
            return _result(correlation, "stop", {"cancelled": cancelled})
        if command == "status":
            return _result(correlation, "status", service.status())
        if command == "ping":
            return {"type": "pong", "id": correlation}
        return {
            "type": "error",
            "id": correlation,
            "detail": f"Unknown command {command!r} (expected speak, stop, status, or ping)",
        }
    except (GatewayError, ValueError) as exc:
        return {"type": "error", "id": correlation, "detail": str(exc)}


async def _handle_speak(
    service: SpeechService, message: dict[str, Any], correlation: Any
) -> dict[str, Any]:
    payload = {key: value for key, value in message.items() if key not in {"type", "id"}}
    try:
        body = SpeakRequest.model_validate({**payload, "wait": False})
    except ValidationError as exc:
        first = exc.errors()[0]
        location = ".".join(str(part) for part in first["loc"]) or "body"
        return {"type": "error", "id": correlation, "detail": f"{location}: {first['msg']}"}
    utterance = await asyncio.to_thread(
        lambda: service.speak(
            body.text,
            provider=body.provider,
            voice=body.voice,
            speed=body.speed,
            options=body.options,
            interrupt=body.interrupt,
        )
    )
    return _result(correlation, "speak", {"utterance": utterance.snapshot()})


def _result(correlation: Any, request: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"type": "result", "id": correlation, "request": request, "data": data}
