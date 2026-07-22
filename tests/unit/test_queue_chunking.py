"""Chunked (pipelined) playback: ordering, look-ahead, interrupt, failure timing.

Exercised with the same controllable doubles as ``test_queue.py``: a
``ControllablePlayer`` the test releases one clip at a time, and plain
per-chunk synthesizers. Each chunk clip carries distinct bytes so the played
order can be checked exactly.
"""

from __future__ import annotations

import threading

import pytest

from tests.conftest import ControllablePlayer
from tts_daemon.core.errors import SynthesisError
from tts_daemon.core.events import EventBus
from tts_daemon.core.models import (
    AudioClip,
    AudioFormat,
    SynthesisRequest,
    Utterance,
    UtteranceState,
)
from tts_daemon.core.queue import PlaybackQueue


@pytest.fixture()
def player() -> ControllablePlayer:
    return ControllablePlayer()


@pytest.fixture()
def queue(player: ControllablePlayer, events: EventBus) -> PlaybackQueue:
    q = PlaybackQueue(player, events, max_size=3, history_size=5)
    yield q
    q.close()


def chunk_clip(label: str) -> AudioClip:
    """A clip whose bytes identify the chunk it came from."""
    return AudioClip(data=label.encode(), format=AudioFormat.WAV)


def utterance(text: str = "long text") -> Utterance:
    return Utterance(SynthesisRequest(text=text), provider_name="test")


def played_bytes(player: ControllablePlayer) -> list[bytes]:
    return [clip.data for clip in player.played]


class TestOrdering:
    def test_chunks_play_in_order_as_one_utterance(
        self, queue: PlaybackQueue, player: ControllablePlayer, recorded_events: list
    ) -> None:
        utt = utterance()
        clips = [chunk_clip(f"c{i}") for i in range(3)]
        queue.submit_chunked(utt, [lambda c=c: c for c in clips])

        for _ in range(3):  # release each chunk in turn
            assert player.play_started.acquire(timeout=2)
            player.finish_current()

        assert utt.wait(timeout=2)
        assert utt.state is UtteranceState.FINISHED
        assert played_bytes(player) == [b"c0", b"c1", b"c2"]

        types = [event.type for event in recorded_events]
        assert types == [
            "utterance.queued",
            "utterance.synthesizing",
            "utterance.speaking",
            "utterance.progress",
            "utterance.progress",
            "utterance.progress",
            "utterance.finished",
        ]

    def test_progress_events_number_the_chunks(
        self, queue: PlaybackQueue, player: ControllablePlayer, recorded_events: list
    ) -> None:
        utt = utterance()
        queue.submit_chunked(utt, [lambda i=i: chunk_clip(f"c{i}") for i in range(3)])
        for _ in range(3):
            assert player.play_started.acquire(timeout=2)
            player.finish_current()
        assert utt.wait(timeout=2)

        progress = [e for e in recorded_events if e.type == "utterance.progress"]
        assert [(e.data["chunk"], e.data["total_chunks"]) for e in progress] == [
            (1, 3),
            (2, 3),
            (3, 3),
        ]
        # Progress carries the same utterance id as the lifecycle events.
        assert {e.data["id"] for e in progress} == {utt.id}


class TestLookAhead:
    def test_next_chunk_synthesizes_while_current_plays(
        self, queue: PlaybackQueue, player: ControllablePlayer
    ) -> None:
        second_synth_ran = threading.Event()

        def synth_first() -> AudioClip:
            return chunk_clip("c0")

        def synth_second() -> AudioClip:
            second_synth_ran.set()
            return chunk_clip("c1")

        utt = utterance()
        queue.submit_chunked(utt, [synth_first, synth_second])

        # Chunk 0 is now blocked in play(); chunk 1 must be synthesized meanwhile.
        assert player.play_started.acquire(timeout=2)
        assert second_synth_ran.wait(timeout=2), "chunk 2 should synthesize during chunk 1 playback"
        assert not utt.wait(timeout=0.05)  # still speaking

        player.finish_current()
        assert player.play_started.acquire(timeout=2)
        player.finish_current()
        assert utt.wait(timeout=2)
        assert utt.state is UtteranceState.FINISHED


class TestInterrupt:
    def test_interrupt_mid_chunk_cancels_the_rest(
        self, queue: PlaybackQueue, player: ControllablePlayer
    ) -> None:
        utt = utterance()
        clips = [chunk_clip(f"c{i}") for i in range(3)]
        queue.submit_chunked(utt, [lambda c=c: c for c in clips])

        assert player.play_started.acquire(timeout=2)  # chunk 0 is playing
        assert queue.clear() >= 1

        assert utt.wait(timeout=2)
        assert utt.state is UtteranceState.CANCELLED
        assert played_bytes(player) == [b"c0"]  # later chunks never reach the player
        assert player.stop_count >= 1


class TestFailureTiming:
    def test_lookahead_failure_waits_for_current_chunk_to_finish(
        self, queue: PlaybackQueue, player: ControllablePlayer
    ) -> None:
        def synth_first() -> AudioClip:
            return chunk_clip("c0")

        def synth_second() -> AudioClip:
            raise SynthesisError("chunk 2 broke")

        utt = utterance()
        queue.submit_chunked(utt, [synth_first, synth_second])

        assert player.play_started.acquire(timeout=2)  # chunk 0 is playing
        # The chunk-2 synthesis has already failed, but the utterance must not
        # fail until chunk 1 has finished playing.
        assert not utt.wait(timeout=0.1)

        player.finish_current()
        assert utt.wait(timeout=2)
        assert utt.state is UtteranceState.FAILED
        assert utt.error == "chunk 2 broke"
        assert played_bytes(player) == [b"c0"]  # chunk 1 did finish playing

    def test_first_chunk_failure_never_reaches_speaking(
        self, queue: PlaybackQueue, player: ControllablePlayer, recorded_events: list
    ) -> None:
        def boom() -> AudioClip:
            raise SynthesisError("first chunk broke")

        utt = utterance()
        queue.submit_chunked(utt, [boom, lambda: chunk_clip("c1")])
        assert utt.wait(timeout=2)
        assert utt.state is UtteranceState.FAILED
        assert utt.error == "first chunk broke"
        assert player.played == []
        types = [event.type for event in recorded_events]
        assert "utterance.speaking" not in types
        assert "utterance.progress" not in types


class TestSingleChunk:
    def test_single_chunk_is_the_plain_path(
        self, queue: PlaybackQueue, player: ControllablePlayer, recorded_events: list
    ) -> None:
        utt = utterance()
        queue.submit_chunked(utt, [lambda: chunk_clip("only")])
        assert player.play_started.acquire(timeout=2)
        player.finish_current()
        assert utt.wait(timeout=2)
        assert utt.state is UtteranceState.FINISHED
        # No look-ahead, so no progress events — identical to submit().
        types = [event.type for event in recorded_events]
        assert types == [
            "utterance.queued",
            "utterance.synthesizing",
            "utterance.speaking",
            "utterance.finished",
        ]

    def test_empty_synthesizers_is_rejected(self, queue: PlaybackQueue) -> None:
        with pytest.raises(ValueError, match="at least one"):
            queue.submit_chunked(utterance(), [])
