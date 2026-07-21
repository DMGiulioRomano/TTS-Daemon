"""Bridge gateway events from the worker thread onto an asyncio queue.

The :class:`~tts_daemon.core.events.EventBus` delivers events synchronously on
the publishing thread (the playback worker). Async consumers — the WebSocket
endpoint (``api/websocket.py``) and the SSE stream (``GET /v1/events``) — need
those events on their own event loop instead.

This helper subscribes to the bus, hops each event onto the loop with
``call_soon_threadsafe``, and buffers them in a bounded queue that drops the
*oldest* event when a slow client falls behind: a laggy observer must never be
able to stall playback.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

from tts_daemon.core.events import Event, EventBus

#: Events buffered per consumer before the oldest is dropped.
DEFAULT_BUFFER = 256


def offer(queue: asyncio.Queue[Event], event: Event) -> None:
    """Enqueue ``event``, dropping the oldest event when the buffer is full."""
    while True:
        try:
            queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()


def subscribe_event_queue(
    events: EventBus,
    loop: asyncio.AbstractEventLoop,
    *,
    maxsize: int = DEFAULT_BUFFER,
) -> tuple[asyncio.Queue[Event], Callable[[], None]]:
    """Subscribe to ``events`` and feed them into a fresh asyncio queue.

    Returns ``(queue, unsubscribe)``. Events published on the worker thread
    are marshalled onto ``loop``; when the loop is already closing (server
    shutdown mid-event) the stray event is silently dropped. The caller owns
    the queue and must call ``unsubscribe`` when the consumer goes away.
    """
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=maxsize)

    def forward(event: Event) -> None:
        # Called on the playback worker thread; hop onto the loop.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(offer, queue, event)

    unsubscribe = events.subscribe(forward)
    return queue, unsubscribe
