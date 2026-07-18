"""PlaybackQueue: ordering, interruption, error isolation, and lifecycle."""

from __future__ import annotations

import pytest

from tests.conftest import ControllablePlayer, make_clip
from tts_gateway.core.errors import PlaybackError, QueueFullError, SynthesisError
from tts_gateway.core.events import EventBus
from tts_gateway.core.models import SynthesisRequest, Utterance, UtteranceState
from tts_gateway.core.queue import PlaybackQueue


@pytest.fixture()
def player() -> ControllablePlayer:
    return ControllablePlayer()


@pytest.fixture()
def queue(player: ControllablePlayer, events: EventBus) -> PlaybackQueue:
    q = PlaybackQueue(player, events, max_size=3, history_size=5)
    yield q
    q.close()


def make_utterance(text: str = "hello") -> Utterance:
    return Utterance(SynthesisRequest(text=text), provider_name="test")


def submit(queue: PlaybackQueue, text: str = "hello") -> Utterance:
    utterance = make_utterance(text)
    queue.submit(utterance, lambda: make_clip(text))
    return utterance


class TestHappyPath:
    def test_single_utterance_full_lifecycle(
        self, queue: PlaybackQueue, player: ControllablePlayer, recorded_events: list
    ) -> None:
        utterance = submit(queue, "one")
        assert player.play_started.acquire(timeout=2)
        player.finish_current()
        assert utterance.wait(timeout=2)
        assert utterance.state is UtteranceState.FINISHED
        assert [event.type for event in recorded_events] == [
            "utterance.queued",
            "utterance.synthesizing",
            "utterance.speaking",
            "utterance.finished",
        ]
        speaking = recorded_events[2]
        assert speaking.data["duration_seconds"] > 0

    def test_fifo_order(self, queue: PlaybackQueue, player: ControllablePlayer) -> None:
        first = submit(queue, "first")
        second = submit(queue, "second")
        assert player.play_started.acquire(timeout=2)
        assert not second.wait(timeout=0.05), "second must wait for first"
        player.finish_current()
        assert first.wait(timeout=2)
        assert player.play_started.acquire(timeout=2)
        player.finish_current()
        assert second.wait(timeout=2)
        assert [clip_text(player, 0), clip_text(player, 1)] == ["first", "second"]

    def test_snapshot_shows_current_and_queued(
        self, queue: PlaybackQueue, player: ControllablePlayer
    ) -> None:
        current = submit(queue, "current")
        queued = submit(queue, "queued")
        assert player.play_started.acquire(timeout=2)
        snapshot = queue.snapshot()
        assert snapshot["current"]["id"] == current.id
        assert [item["id"] for item in snapshot["queued"]] == [queued.id]
        assert snapshot["max_size"] == 3
        player.finish_current()
        player.play_started.acquire(timeout=2)
        player.finish_current()

    def test_find_across_stages(self, queue: PlaybackQueue, player: ControllablePlayer) -> None:
        current = submit(queue, "current")
        pending = submit(queue, "pending")
        assert player.play_started.acquire(timeout=2)
        assert queue.find(current.id)["state"] == "speaking"
        assert queue.find(pending.id)["state"] == "queued"
        assert queue.find("bogus") is None
        player.finish_current()
        assert current.wait(timeout=2)
        player.play_started.acquire(timeout=2)
        player.finish_current()
        assert pending.wait(timeout=2)
        assert queue.find(current.id)["state"] == "finished"  # from history


class TestInterrupt:
    def test_clear_cancels_current_and_pending(
        self, queue: PlaybackQueue, player: ControllablePlayer, recorded_events: list
    ) -> None:
        current = submit(queue, "current")
        pending = submit(queue, "pending")
        assert player.play_started.acquire(timeout=2)
        cancelled = queue.clear()
        assert cancelled == 2
        assert current.wait(timeout=2)
        assert current.state is UtteranceState.CANCELLED
        assert pending.state is UtteranceState.CANCELLED
        assert player.stop_count == 1
        types = [event.type for event in recorded_events]
        assert types.count("utterance.cancelled") == 2
        assert "queue.cleared" in types

    def test_clear_on_idle_queue_is_zero(self, queue: PlaybackQueue) -> None:
        assert queue.clear() == 0

    def test_queue_keeps_working_after_clear(
        self, queue: PlaybackQueue, player: ControllablePlayer
    ) -> None:
        submit(queue, "first")
        assert player.play_started.acquire(timeout=2)
        queue.clear()
        after = submit(queue, "after")
        assert player.play_started.acquire(timeout=2)
        player.finish_current()
        assert after.wait(timeout=2)
        assert after.state is UtteranceState.FINISHED

    def test_cancel_requested_before_worker_reaches_item(
        self, queue: PlaybackQueue, player: ControllablePlayer
    ) -> None:
        blocker = submit(queue, "blocker")
        assert player.play_started.acquire(timeout=2)
        victim = submit(queue, "victim")
        victim.request_cancel()
        player.finish_current()
        assert blocker.wait(timeout=2)
        assert victim.wait(timeout=2)
        assert victim.state is UtteranceState.CANCELLED
        # never synthesized, never played
        assert len(player.played) == 1


class TestErrors:
    def test_synthesis_error_fails_utterance_but_not_queue(
        self, queue: PlaybackQueue, player: ControllablePlayer, recorded_events: list
    ) -> None:
        failing = make_utterance("broken")

        def explode() -> None:
            raise SynthesisError("engine says no")

        queue.submit(failing, explode)
        healthy = submit(queue, "healthy")
        assert failing.wait(timeout=2)
        assert failing.state is UtteranceState.FAILED
        assert failing.error == "engine says no"
        assert player.play_started.acquire(timeout=2)
        player.finish_current()
        assert healthy.wait(timeout=2)
        assert healthy.state is UtteranceState.FINISHED
        assert "utterance.failed" in [event.type for event in recorded_events]

    def test_provider_crash_is_contained(self, queue: PlaybackQueue) -> None:
        utterance = make_utterance("crash")

        def crash() -> None:
            raise RuntimeError("unexpected bug")

        queue.submit(utterance, crash)
        assert utterance.wait(timeout=2)
        assert utterance.state is UtteranceState.FAILED
        assert "provider crashed" in utterance.error

    def test_playback_error_fails_utterance(self, events: EventBus) -> None:
        class BrokenPlayer(ControllablePlayer):
            def play(self, clip):
                raise PlaybackError("no sound device")

        queue = PlaybackQueue(BrokenPlayer(), events, max_size=3, history_size=5)
        try:
            utterance = make_utterance("x")
            queue.submit(utterance, lambda: make_clip("x"))
            assert utterance.wait(timeout=2)
            assert utterance.state is UtteranceState.FAILED
            assert "no sound device" in utterance.error
        finally:
            queue.close()

    def test_player_crash_is_contained(self, events: EventBus) -> None:
        class CrashingPlayer(ControllablePlayer):
            def play(self, clip):
                raise ZeroDivisionError("player bug")

        queue = PlaybackQueue(CrashingPlayer(), events, max_size=3, history_size=5)
        try:
            utterance = make_utterance("x")
            queue.submit(utterance, lambda: make_clip("x"))
            assert utterance.wait(timeout=2)
            assert utterance.state is UtteranceState.FAILED
            assert "player crashed" in utterance.error
        finally:
            queue.close()


class TestCapacityAndLifecycle:
    def test_queue_full(self, queue: PlaybackQueue, player: ControllablePlayer) -> None:
        submit(queue, "playing")  # occupies the worker
        assert player.play_started.acquire(timeout=2)
        for i in range(3):  # fill max_size=3
            submit(queue, f"pending-{i}")
        with pytest.raises(QueueFullError, match="full"):
            submit(queue, "overflow")
        queue.clear()

    def test_history_bounded_and_ordered(
        self, queue: PlaybackQueue, player: ControllablePlayer
    ) -> None:
        texts = [f"utt-{i}" for i in range(7)]
        for text in texts:
            utterance = submit(queue, text)
            assert player.play_started.acquire(timeout=2)
            player.finish_current()
            assert utterance.wait(timeout=2)
        history = queue.snapshot()["history"]
        assert len(history) == 5  # history_size=5
        assert [item["text"] for item in history] == texts[-5:]

    def test_close_cancels_everything_and_is_idempotent(
        self, player: ControllablePlayer, events: EventBus
    ) -> None:
        queue = PlaybackQueue(player, events, max_size=3, history_size=5)
        current = make_utterance("current")
        queue.submit(current, lambda: make_clip("current"))
        assert player.play_started.acquire(timeout=2)
        pending = make_utterance("pending")
        queue.submit(pending, lambda: make_clip("pending"))
        queue.close()
        queue.close()  # idempotent
        assert current.wait(timeout=2)
        assert pending.state is UtteranceState.CANCELLED
        with pytest.raises(RuntimeError, match="closed"):
            queue.submit(make_utterance("late"), lambda: make_clip("late"))


def clip_text(player: ControllablePlayer, index: int) -> str:
    """Recover which text a played clip was made from (durations differ)."""
    # ControllablePlayer stores clips in play order; conftest's make_clip
    # encodes the text length in the audio duration, so equality of data
    # against a rebuilt clip identifies the source text.
    clip = player.played[index]
    for text in ("first", "second"):
        if make_clip(text).data == clip.data:
            return text
    return "unknown"
