"""Domain models: AudioClip inspection and the Utterance state machine."""

from __future__ import annotations

from tests.conftest import make_clip
from tts_gateway.core.models import (
    AudioClip,
    AudioFormat,
    Availability,
    SynthesisRequest,
    Utterance,
    UtteranceState,
)


class TestAudioClip:
    def test_wav_duration_is_computed(self) -> None:
        clip = make_clip("three short words")
        assert clip.duration_seconds is not None
        assert clip.duration_seconds > 0

    def test_non_wav_duration_is_none(self) -> None:
        clip = AudioClip(data=b"not audio", format=AudioFormat.MP3)
        assert clip.duration_seconds is None
        assert clip.media_type == "audio/mpeg"

    def test_corrupt_wav_duration_is_none(self) -> None:
        assert AudioClip(data=b"RIFFgarbage").duration_seconds is None

    def test_format_helpers(self) -> None:
        assert AudioFormat.WAV.media_type == "audio/wav"
        assert AudioFormat.OGG.suffix == ".ogg"


class TestAvailability:
    def test_constructors(self) -> None:
        assert Availability.ok().available
        broken = Availability.unavailable("no binary")
        assert not broken.available
        assert broken.reason == "no binary"


class TestUtterance:
    def make(self) -> Utterance:
        return Utterance(SynthesisRequest(text="hi", voice="v"), provider_name="tone")

    def test_initial_state(self) -> None:
        utterance = self.make()
        assert utterance.state is UtteranceState.QUEUED
        assert not utterance.state.is_terminal
        assert not utterance.cancel_requested
        assert not utterance.wait(timeout=0)

    def test_happy_path_transitions(self) -> None:
        utterance = self.make()
        assert utterance.transition(UtteranceState.SYNTHESIZING)
        assert utterance.transition(UtteranceState.SPEAKING)
        assert utterance.started_at is not None
        assert utterance.transition(UtteranceState.FINISHED)
        assert utterance.finished_at is not None
        assert utterance.wait(timeout=0)

    def test_terminal_states_latch(self) -> None:
        utterance = self.make()
        assert utterance.transition(UtteranceState.CANCELLED)
        # once terminal, nothing can resurrect it
        assert not utterance.transition(UtteranceState.SPEAKING)
        assert not utterance.transition(UtteranceState.FINISHED)
        assert utterance.state is UtteranceState.CANCELLED

    def test_failed_records_error(self) -> None:
        utterance = self.make()
        utterance.transition(UtteranceState.FAILED, error="engine exploded")
        assert utterance.error == "engine exploded"
        snapshot = utterance.snapshot()
        assert snapshot["state"] == "failed"
        assert snapshot["error"] == "engine exploded"

    def test_snapshot_is_json_safe(self) -> None:
        import json

        snapshot = self.make().snapshot()
        assert json.loads(json.dumps(snapshot)) == snapshot
        assert snapshot["text"] == "hi"
        assert snapshot["voice"] == "v"
        assert snapshot["provider"] == "tone"

    def test_request_cancel_sets_flag_only(self) -> None:
        utterance = self.make()
        utterance.request_cancel()
        assert utterance.cancel_requested
        assert utterance.state is UtteranceState.QUEUED
