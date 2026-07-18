"""A minimal thread-safe publish/subscribe event bus.

The queue worker publishes utterance lifecycle events from its own thread;
subscribers (WebSocket bridges, tests, log sinks) receive them synchronously
on that thread. Subscribers that need to hop threads or event loops do so
themselves (see ``tts_gateway.api.websocket``), which keeps this module free
of asyncio.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Event:
    """A single gateway event.

    ``type`` is a dotted name such as ``utterance.finished`` or
    ``queue.cleared``; ``data`` is a JSON-safe payload.
    """

    type: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "data": self.data, "timestamp": self.timestamp}


EventHandler = Callable[[Event], None]


class EventBus:
    """Synchronous fan-out of events to registered handlers.

    Handlers are called on the publishing thread. A handler that raises does
    not affect other handlers; the exception is logged and swallowed, because
    an observer must never be able to break playback.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: list[EventHandler] = []

    def subscribe(self, handler: EventHandler) -> Callable[[], None]:
        """Register ``handler`` and return a callable that unsubscribes it."""
        with self._lock:
            self._handlers.append(handler)

        def unsubscribe() -> None:
            # unsubscribing twice is harmless by contract
            with self._lock, contextlib.suppress(ValueError):
                self._handlers.remove(handler)

        return unsubscribe

    def publish(self, type_: str, data: dict[str, Any] | None = None) -> Event:
        """Build an :class:`Event` and deliver it to every subscriber."""
        event = Event(type=type_, data=data or {})
        with self._lock:
            handlers = list(self._handlers)
        for handler in handlers:
            try:
                handler(event)
            except Exception:
                logger.exception("Event handler %r failed for event %s", handler, event.type)
        return event

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._handlers)
