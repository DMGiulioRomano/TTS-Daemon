"""Bridge gateway events from the worker thread onto an asyncio loop.

Gateway events are published synchronously on the playback worker thread (see
:mod:`tts_daemon.core.events`). Both the WebSocket endpoint and the SSE
endpoint need those events as items in an :class:`asyncio.Queue` on the API
event loop; this module is the single place that does the thread hop, with a
bounded buffer that drops the oldest event when a slow client falls behind so
that a stalled connection can never block playback.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable

from tts_daemon.core.events import Event
from tts_daemon.core.service import SpeechService

#: Events buffered per connection before the oldest is dropped.
DEFAULT_BUFFER = 256


def _offer(queue: asyncio.Queue[Event], event: Event) -> None:
    """Enqueue ``event``, dropping the oldest when the buffer is full."""
    while True:
        try:
            queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()


def subscribe_async_queue(
    service: SpeechService,
    loop: asyncio.AbstractEventLoop,
    *,
    buffer: int = DEFAULT_BUFFER,
) -> tuple[asyncio.Queue[Event], Callable[[], None]]:
    """Subscribe to gateway events, delivered into an asyncio queue.

    Returns the queue and an ``unsubscribe`` callable. The subscription runs
    the event handler on the publishing (worker) thread and hops each event
    onto ``loop`` via ``call_soon_threadsafe``; the loop may already be
    closing during shutdown, which is suppressed. Call ``unsubscribe`` from
    the loop when the client disconnects.
    """
    queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=buffer)

    def forward(event: Event) -> None:
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(_offer, queue, event)

    unsubscribe = service.events.subscribe(forward)
    return queue, unsubscribe
