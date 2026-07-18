"""The playback queue: ordered, interruptible speech.

A single worker thread drains the queue. For each utterance it synthesizes
(via a callable bound to the chosen provider) and then plays the clip; both
steps happen on the worker so utterances are spoken strictly in submission
order and the API thread never blocks.

Interruption is cooperative: :meth:`PlaybackQueue.clear` flags every pending
utterance, stops the player (which unblocks the worker mid-clip), and the
worker checks the flag at each checkpoint. Cancellation between checkpoints
is therefore prompt but never tears a thread down mid-call.

Locking rules (the reason this module stays simple):

- ``_condition`` guards the deque, ``_current``, ``_history`` and ``_closed``.
- Events are published and utterance state transitions happen **outside**
  ``_condition``, so event handlers may safely call :meth:`snapshot`.
- Event *ordering* across threads is best-effort; every event carries a full
  utterance snapshot taken at publish time, which is authoritative.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable
from typing import Any

from tts_gateway.core.errors import PlaybackError, QueueFullError, SynthesisError
from tts_gateway.core.events import EventBus
from tts_gateway.core.interfaces import AudioPlayer
from tts_gateway.core.models import AudioClip, Utterance, UtteranceState

logger = logging.getLogger(__name__)

SynthesizeFn = Callable[[], AudioClip]


class PlaybackQueue:
    """FIFO queue of utterances with a dedicated playback worker thread."""

    def __init__(
        self,
        player: AudioPlayer,
        events: EventBus,
        *,
        max_size: int = 64,
        history_size: int = 50,
    ) -> None:
        self._player = player
        self._events = events
        self._max_size = max_size
        self._condition = threading.Condition()
        self._items: deque[tuple[Utterance, SynthesizeFn]] = deque()
        self._history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._current: Utterance | None = None
        self._closed = False
        self._worker = threading.Thread(target=self._run, name="tts-gateway-playback", daemon=True)
        self._worker.start()

    # ------------------------------------------------------------------ api

    def submit(self, utterance: Utterance, synthesize: SynthesizeFn) -> None:
        """Enqueue an utterance; raises :class:`QueueFullError` at capacity."""
        with self._condition:
            if self._closed:
                raise RuntimeError("PlaybackQueue is closed")
            if len(self._items) >= self._max_size:
                raise QueueFullError(
                    f"Playback queue is full ({self._max_size} utterances); "
                    "wait, or POST /v1/stop to clear it"
                )
            self._items.append((utterance, synthesize))
            snapshot = utterance.snapshot()
            self._condition.notify()
        self._events.publish("utterance.queued", snapshot)

    def clear(self) -> int:
        """Cancel all pending utterances and interrupt the one playing.

        Returns the number of utterances affected (pending + current). The
        current utterance is flagged and the player stopped; its terminal
        transition is performed by the worker, keeping one owner for the
        state machine.
        """
        with self._condition:
            pending = list(self._items)
            self._items.clear()
            current = self._current
        cancelled = 0
        for utterance, _ in pending:
            utterance.request_cancel()
            if utterance.transition(UtteranceState.CANCELLED):
                cancelled += 1
                snapshot = utterance.snapshot()
                with self._condition:
                    self._history.append(snapshot)
                self._events.publish("utterance.cancelled", snapshot)
        if current is not None and not current.state.is_terminal:
            current.request_cancel()
            self._player.stop()
            cancelled += 1
        self._events.publish("queue.cleared", {"cancelled": cancelled})
        return cancelled

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe view of the queue for ``/v1/status``."""
        with self._condition:
            return {
                "current": self._current.snapshot() if self._current else None,
                "queued": [utterance.snapshot() for utterance, _ in self._items],
                "history": list(self._history),
                "size": len(self._items),
                "max_size": self._max_size,
            }

    def find(self, utterance_id: str) -> dict[str, Any] | None:
        """Snapshot of a live or recently finished utterance, by id."""
        with self._condition:
            if self._current is not None and self._current.id == utterance_id:
                return self._current.snapshot()
            for utterance, _ in self._items:
                if utterance.id == utterance_id:
                    return utterance.snapshot()
            for snapshot in reversed(self._history):
                if snapshot["id"] == utterance_id:
                    return snapshot
        return None

    def close(self, timeout: float = 5.0) -> None:
        """Cancel everything and stop the worker thread. Idempotent."""
        with self._condition:
            if self._closed:
                return
            self._closed = True
        self.clear()
        with self._condition:
            self._condition.notify_all()
        self._worker.join(timeout=timeout)
        if self._worker.is_alive():  # pragma: no cover - only on a wedged player
            logger.warning("Playback worker did not stop within %.1fs", timeout)

    # --------------------------------------------------------------- worker

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._items and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                utterance, synthesize = self._items.popleft()
                self._current = utterance
            try:
                self._process(utterance, synthesize)
            except Exception:  # never let a bug kill the playback thread
                logger.exception("Unexpected error processing utterance %s", utterance.id)
                self._finalize(utterance, UtteranceState.FAILED, error="internal error")
            finally:
                with self._condition:
                    self._current = None
                    self._history.append(utterance.snapshot())

    def _process(self, utterance: Utterance, synthesize: SynthesizeFn) -> None:
        if utterance.cancel_requested:
            self._finalize(utterance, UtteranceState.CANCELLED)
            return

        if not utterance.transition(UtteranceState.SYNTHESIZING):
            return
        self._events.publish("utterance.synthesizing", utterance.snapshot())
        try:
            clip = synthesize()
        except SynthesisError as exc:
            self._finalize(utterance, UtteranceState.FAILED, error=str(exc))
            return
        except Exception as exc:
            logger.exception("Provider crashed synthesizing utterance %s", utterance.id)
            self._finalize(utterance, UtteranceState.FAILED, error=f"provider crashed: {exc}")
            return

        if utterance.cancel_requested:
            self._finalize(utterance, UtteranceState.CANCELLED)
            return

        if not utterance.transition(UtteranceState.SPEAKING):
            return
        speaking_snapshot = utterance.snapshot()
        speaking_snapshot["duration_seconds"] = clip.duration_seconds
        self._events.publish("utterance.speaking", speaking_snapshot)
        try:
            completed = self._player.play(clip)
        except PlaybackError as exc:
            self._finalize(utterance, UtteranceState.FAILED, error=str(exc))
            return
        except Exception as exc:
            logger.exception("Player crashed playing utterance %s", utterance.id)
            self._finalize(utterance, UtteranceState.FAILED, error=f"player crashed: {exc}")
            return

        if completed and not utterance.cancel_requested:
            self._finalize(utterance, UtteranceState.FINISHED)
        else:
            self._finalize(utterance, UtteranceState.CANCELLED)

    def _finalize(
        self, utterance: Utterance, state: UtteranceState, *, error: str | None = None
    ) -> None:
        if utterance.transition(state, error=error):
            self._events.publish(f"utterance.{state.value}", utterance.snapshot())
