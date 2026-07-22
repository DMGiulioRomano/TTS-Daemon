"""The playback queue: ordered, interruptible speech.

A single worker thread drains the queue. For each utterance it synthesizes
(via a callable bound to the chosen provider) and then plays the clip; both
steps happen on the worker so utterances are spoken strictly in submission
order and the API thread never blocks.

Interruption is cooperative: :meth:`PlaybackQueue.clear` flags every pending
utterance, stops the player (which unblocks the worker mid-clip), and the
worker checks the flag at each checkpoint. Cancellation between checkpoints
is therefore prompt but never tears a thread down mid-call.

A long utterance may be submitted as several sentence *chunks* (see
:meth:`PlaybackQueue.submit_chunked`): the worker speaks chunk N while chunk
N+1 synthesizes on a single-slot ``ThreadPoolExecutor`` it owns, so
time-to-first-sound drops without a second worker thread or any change to the
utterance state machine. The look-ahead is depth one, and cancellation is
still checked between every chunk.

Locking rules (the reason this module stays simple):

- ``_condition`` guards the deque, ``_current``, ``_history`` and ``_closed``.
- Events are published and utterance state transitions happen **outside**
  ``_condition``, so event handlers may safely call :meth:`snapshot`.
- The prefetch executor is only ever driven by the one worker thread (one
  chunk in flight at a time), so it needs no extra lock; it is never touched
  while ``_condition`` is held.
- Event *ordering* across threads is best-effort; every event carries a full
  utterance snapshot taken at publish time, which is authoritative.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

from tts_daemon.core.errors import PlaybackError, QueueFullError, SynthesisError
from tts_daemon.core.events import EventBus
from tts_daemon.core.interfaces import AudioPlayer
from tts_daemon.core.models import AudioClip, Utterance, UtteranceState

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
        self._items: deque[tuple[Utterance, list[SynthesizeFn]]] = deque()
        self._history: deque[dict[str, Any]] = deque(maxlen=history_size)
        self._current: Utterance | None = None
        self._closed = False
        # Single-slot look-ahead synthesis for chunked utterances. Driven only
        # by the worker thread; the thread is spawned lazily on first use, so
        # non-chunked queues (and idle ones) never pay for it.
        self._prefetch = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts-daemon-prefetch")
        self._worker = threading.Thread(target=self._run, name="tts-daemon-playback", daemon=True)
        self._worker.start()

    # ------------------------------------------------------------------ api

    def submit(self, utterance: Utterance, synthesize: SynthesizeFn) -> None:
        """Enqueue an utterance synthesized as a single clip.

        Raises :class:`QueueFullError` at capacity.
        """
        self._enqueue(utterance, [synthesize])

    def submit_chunked(self, utterance: Utterance, synthesizers: Sequence[SynthesizeFn]) -> None:
        """Enqueue an utterance whose audio comes from several sentence chunks.

        The chunks play in order as one utterance (one id, one lifecycle), with
        chunk N+1 synthesized while chunk N plays. A single-element sequence is
        equivalent to :meth:`submit`.
        """
        if not synthesizers:
            raise ValueError("submit_chunked requires at least one synthesizer")
        self._enqueue(utterance, list(synthesizers))

    def _enqueue(self, utterance: Utterance, synthesizers: list[SynthesizeFn]) -> None:
        with self._condition:
            if self._closed:
                raise RuntimeError("PlaybackQueue is closed")
            if len(self._items) >= self._max_size:
                raise QueueFullError(
                    f"Playback queue is full ({self._max_size} utterances); "
                    "wait, or POST /v1/stop to clear it"
                )
            self._items.append((utterance, synthesizers))
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
        # The worker has stopped, so nothing else submits prefetch work; don't
        # block on a look-ahead synthesis that may still be running.
        self._prefetch.shutdown(wait=False)

    # --------------------------------------------------------------- worker

    def _run(self) -> None:
        while True:
            with self._condition:
                while not self._items and not self._closed:
                    self._condition.wait()
                if self._closed:
                    return
                utterance, synthesizers = self._items.popleft()
                self._current = utterance
            try:
                self._process(utterance, synthesizers)
            except Exception:  # never let a bug kill the playback thread
                logger.exception("Unexpected error processing utterance %s", utterance.id)
                self._finalize(utterance, UtteranceState.FAILED, error="internal error")
            finally:
                with self._condition:
                    self._current = None
                    self._history.append(utterance.snapshot())

    def _process(self, utterance: Utterance, synthesizers: list[SynthesizeFn]) -> None:
        if len(synthesizers) == 1:
            self._process_single(utterance, synthesizers[0])
        else:
            self._process_chunked(utterance, synthesizers)

    def _process_single(self, utterance: Utterance, synthesize: SynthesizeFn) -> None:
        if utterance.cancel_requested:
            self._finalize(utterance, UtteranceState.CANCELLED)
            return

        if not utterance.transition(UtteranceState.SYNTHESIZING):
            return
        self._events.publish("utterance.synthesizing", utterance.snapshot())
        clip = self._synthesized(utterance, synthesize)
        if clip is None:
            return

        if utterance.cancel_requested:
            self._finalize(utterance, UtteranceState.CANCELLED)
            return

        if not utterance.transition(UtteranceState.SPEAKING):
            return
        speaking_snapshot = utterance.snapshot()
        speaking_snapshot["duration_seconds"] = clip.duration_seconds
        self._events.publish("utterance.speaking", speaking_snapshot)

        completed = self._played(utterance, clip)
        if completed is None:
            return  # a playback failure was already finalized
        if completed and not utterance.cancel_requested:
            self._finalize(utterance, UtteranceState.FINISHED)
        else:
            self._finalize(utterance, UtteranceState.CANCELLED)

    def _process_chunked(self, utterance: Utterance, synthesizers: list[SynthesizeFn]) -> None:
        """Speak an utterance as ordered chunks with one clip of look-ahead.

        The state machine is unchanged: a single SYNTHESIZING then SPEAKING
        transition, then FINISHED (or CANCELLED / FAILED). Chunk N+1 synthesizes
        on the prefetch executor while chunk N plays; a look-ahead synthesis
        failure therefore only surfaces once the current chunk has finished
        playing, and cancellation is honoured before every chunk.
        """
        total = len(synthesizers)
        if utterance.cancel_requested:
            self._finalize(utterance, UtteranceState.CANCELLED)
            return

        if not utterance.transition(UtteranceState.SYNTHESIZING):
            return
        self._events.publish("utterance.synthesizing", utterance.snapshot())
        clip = self._synthesized(utterance, synthesizers[0])
        if clip is None:
            return

        if utterance.cancel_requested:
            self._finalize(utterance, UtteranceState.CANCELLED)
            return
        if not utterance.transition(UtteranceState.SPEAKING):
            return
        speaking_snapshot = utterance.snapshot()
        speaking_snapshot["duration_seconds"] = clip.duration_seconds
        self._events.publish("utterance.speaking", speaking_snapshot)

        next_future: Future[AudioClip] | None = None
        for index in range(total):
            is_last = index + 1 == total
            # Kick off chunk N+1 before playing chunk N, so synthesis overlaps
            # playback (the whole point). Skipped once cancellation is pending.
            if not is_last and next_future is None and not utterance.cancel_requested:
                next_future = self._prefetch.submit(synthesizers[index + 1])

            if utterance.cancel_requested:
                self._cancel(next_future)
                self._finalize(utterance, UtteranceState.CANCELLED)
                return

            progress = utterance.snapshot()
            progress["chunk"] = index + 1
            progress["total_chunks"] = total
            self._events.publish("utterance.progress", progress)

            completed = self._played(utterance, clip)
            if completed is None:
                self._cancel(next_future)
                return  # playback failure already finalized
            if not completed or utterance.cancel_requested:
                self._cancel(next_future)
                self._finalize(utterance, UtteranceState.CANCELLED)
                return

            if not is_last and next_future is not None:
                # Blocks only if chunk N+1 is not synthesized yet; a failure
                # here surfaces now, after chunk N has finished playing.
                clip = self._synthesized(utterance, next_future.result)
                next_future = None
                if clip is None:
                    return  # a chunk failed to synthesize; already finalized

        self._finalize(utterance, UtteranceState.FINISHED)

    def _synthesized(self, utterance: Utterance, produce: SynthesizeFn) -> AudioClip | None:
        """Run a chunk synthesis (a direct call or ``future.result``).

        Returns the clip, or ``None`` after finalizing the utterance FAILED —
        the same error mapping the single-clip path has always used.
        """
        try:
            return produce()
        except SynthesisError as exc:
            self._finalize(utterance, UtteranceState.FAILED, error=str(exc))
        except Exception as exc:
            logger.exception("Provider crashed synthesizing utterance %s", utterance.id)
            self._finalize(utterance, UtteranceState.FAILED, error=f"provider crashed: {exc}")
        return None

    def _played(self, utterance: Utterance, clip: AudioClip) -> bool | None:
        """Play one clip. Returns whether it finished, or ``None`` on failure.

        ``None`` means a PlaybackError (or player crash) was mapped to FAILED;
        ``True``/``False`` mean the clip finished / was stopped mid-play.
        """
        try:
            return self._player.play(clip)
        except PlaybackError as exc:
            self._finalize(utterance, UtteranceState.FAILED, error=str(exc))
        except Exception as exc:
            logger.exception("Player crashed playing utterance %s", utterance.id)
            self._finalize(utterance, UtteranceState.FAILED, error=f"player crashed: {exc}")
        return None

    @staticmethod
    def _cancel(future: Future[AudioClip] | None) -> None:
        """Best-effort cancel of a pending look-ahead synthesis."""
        if future is not None:
            future.cancel()

    def _finalize(
        self, utterance: Utterance, state: UtteranceState, *, error: str | None = None
    ) -> None:
        if utterance.transition(state, error=error):
            self._events.publish(f"utterance.{state.value}", utterance.snapshot())
