"""EdgeProvider tested with the edge-tts package mocked (fully hermetic).

The real package talks to Microsoft over the network; here a fake ``edge_tts``
module is injected into ``sys.modules`` so nothing leaves the machine. Tests that
assert the "not installed" behaviour simply omit the fixture — the extra is not
part of the dev install.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from tts_daemon.core.errors import SynthesisError
from tts_daemon.core.models import AudioFormat, SynthesisRequest
from tts_daemon.providers.edge import EdgeProvider, _rate_from_speed

DEFAULT_VOICES = [
    {
        "ShortName": "en-US-AriaNeural",
        "FriendlyName": "Microsoft Aria Online (Natural)",
        "Locale": "en-US",
        "Gender": "Female",
    },
    {
        "ShortName": "it-IT-ElsaNeural",
        "FriendlyName": "Microsoft Elsa Online (Natural)",
        "Locale": "it-IT",
        "Gender": "Female",
    },
]


@pytest.fixture()
def fake_edge(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Install a configurable fake ``edge_tts`` module; return its control state."""
    state = SimpleNamespace(
        audio=[b"ID3", b"-fake-mp3-bytes-"],
        calls=[],
        voices=list(DEFAULT_VOICES),
        stream_error=None,
        list_voices_error=None,
        list_voices_count=0,
    )

    class Communicate:
        def __init__(
            self,
            text: str,
            voice: str,
            *,
            rate: str = "+0%",
            pitch: str = "+0Hz",
            volume: str = "+0%",
        ) -> None:
            self.text = text
            self.voice = voice
            self.rate = rate
            self.pitch = pitch
            self.volume = volume
            state.calls.append(self)

        async def stream(self):
            if state.stream_error is not None:
                raise state.stream_error
            for data in state.audio:
                yield {"type": "audio", "data": data}
                yield {"type": "WordBoundary", "offset": 0}

    async def list_voices():
        state.list_voices_count += 1
        if state.list_voices_error is not None:
            raise state.list_voices_error
        return state.voices

    module = types.ModuleType("edge_tts")
    module.Communicate = Communicate
    module.list_voices = list_voices
    monkeypatch.setitem(sys.modules, "edge_tts", module)
    return state


class TestRateMapping:
    @pytest.mark.parametrize(
        ("speed", "expected"),
        [(1.0, "+0%"), (1.5, "+50%"), (0.5, "-50%"), (2.0, "+100%"), (1.25, "+25%")],
    )
    def test_rate_from_speed(self, speed: float, expected: str) -> None:
        assert _rate_from_speed(speed) == expected


class TestSynthesis:
    def test_returns_mp3_clip(self, fake_edge: SimpleNamespace) -> None:
        clip = EdgeProvider().synthesize(SynthesisRequest(text="hello"))
        assert clip.format is AudioFormat.MP3
        assert clip.data == b"".join(fake_edge.audio)
        call = fake_edge.calls[-1]
        assert call.text == "hello"
        assert call.voice == "en-US-AriaNeural"  # default voice
        assert call.rate == "+0%"

    def test_speed_maps_to_rate(self, fake_edge: SimpleNamespace) -> None:
        EdgeProvider().synthesize(SynthesisRequest(text="x", speed=1.5))
        assert fake_edge.calls[-1].rate == "+50%"

    def test_voice_override(self, fake_edge: SimpleNamespace) -> None:
        EdgeProvider().synthesize(SynthesisRequest(text="x", voice="it-IT-ElsaNeural"))
        assert fake_edge.calls[-1].voice == "it-IT-ElsaNeural"

    def test_pitch_and_volume_passthrough(self, fake_edge: SimpleNamespace) -> None:
        EdgeProvider().synthesize(
            SynthesisRequest(text="x", options={"pitch": "+10Hz", "volume": "-5%"})
        )
        call = fake_edge.calls[-1]
        assert call.pitch == "+10Hz"
        assert call.volume == "-5%"

    def test_settings_defaults_for_pitch_volume(self, fake_edge: SimpleNamespace) -> None:
        provider = EdgeProvider({"default_pitch": "+5Hz", "default_volume": "+20%"})
        provider.synthesize(SynthesisRequest(text="x"))
        call = fake_edge.calls[-1]
        assert call.pitch == "+5Hz"
        assert call.volume == "+20%"

    def test_request_option_overrides_setting_default(self, fake_edge: SimpleNamespace) -> None:
        provider = EdgeProvider({"default_pitch": "+5Hz"})
        provider.synthesize(SynthesisRequest(text="x", options={"pitch": "-3Hz"}))
        assert fake_edge.calls[-1].pitch == "-3Hz"

    def test_unknown_option_rejected(self, fake_edge: SimpleNamespace) -> None:
        with pytest.raises(SynthesisError, match="Unknown edge options: emotion"):
            EdgeProvider().synthesize(SynthesisRequest(text="x", options={"emotion": "sad"}))

    def test_invalid_speed_rejected(self, fake_edge: SimpleNamespace) -> None:
        with pytest.raises(SynthesisError, match="speed must be positive"):
            EdgeProvider().synthesize(SynthesisRequest(text="x", speed=0))

    def test_empty_audio_is_an_error(self, fake_edge: SimpleNamespace) -> None:
        fake_edge.audio = []
        with pytest.raises(SynthesisError, match="returned no audio"):
            EdgeProvider().synthesize(SynthesisRequest(text="x", voice="bogus-voice"))

    def test_stream_error_becomes_actionable_synthesis_error(
        self, fake_edge: SimpleNamespace
    ) -> None:
        fake_edge.stream_error = RuntimeError("connection reset")
        with pytest.raises(SynthesisError, match="cloud provider") as excinfo:
            EdgeProvider().synthesize(SynthesisRequest(text="x"))
        assert "connection reset" in str(excinfo.value)


class TestVoices:
    def test_lists_and_maps_voices(self, fake_edge: SimpleNamespace) -> None:
        voices = {voice.id: voice for voice in EdgeProvider().voices()}
        assert set(voices) == {"en-US-AriaNeural", "it-IT-ElsaNeural"}
        aria = voices["en-US-AriaNeural"]
        assert aria.language == "en-US"
        assert aria.name == "Microsoft Aria Online (Natural)"
        assert aria.description == "Female"

    def test_voices_are_cached(self, fake_edge: SimpleNamespace) -> None:
        provider = EdgeProvider()
        first = provider.voices()
        second = provider.voices()
        assert first is second
        assert fake_edge.list_voices_count == 1  # fetched once

    def test_voices_empty_when_listing_fails(self, fake_edge: SimpleNamespace) -> None:
        fake_edge.list_voices_error = RuntimeError("offline")
        assert EdgeProvider().voices() == []


class TestAvailabilityWithPackage:
    def test_available_when_package_present(self, fake_edge: SimpleNamespace) -> None:
        assert EdgeProvider().availability().available


class TestWithoutPackage:
    def test_availability_reports_missing_package(self) -> None:
        assert "edge_tts" not in sys.modules or sys.modules["edge_tts"] is None
        availability = EdgeProvider().availability()
        assert not availability.available
        assert "tts-daemon[edge]" in availability.reason

    def test_voices_empty_without_package(self) -> None:
        assert EdgeProvider().voices() == []

    def test_synthesize_without_package_raises(self) -> None:
        with pytest.raises(SynthesisError, match="edge-tts is not installed"):
            EdgeProvider().synthesize(SynthesisRequest(text="x"))


class TestMp3PlaybackRouting:
    """The clip is MP3, so the player must route it to an MP3-capable command."""

    def test_command_player_routes_mp3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tts_daemon.players.command as command_module
        from tts_daemon.players.command import CommandPlayer

        # Only ffplay installed; aplay (WAV-only) absent — MP3 must pick ffplay.
        def which(name: str) -> str | None:
            return "/usr/bin/ffplay" if name == "ffplay" else None

        monkeypatch.setattr(command_module.shutil, "which", which)
        argv = CommandPlayer()._argv_for(AudioFormat.MP3)
        assert argv[0] == "ffplay"

    def test_wav_only_command_is_skipped_for_mp3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tts_daemon.players.command as command_module
        from tts_daemon.core.errors import PlaybackError
        from tts_daemon.players.command import CommandPlayer

        # Only aplay (WAV-only) installed: MP3 has no capable command.
        def which(name: str) -> str | None:
            return "/usr/bin/aplay" if name == "aplay" else None

        monkeypatch.setattr(command_module.shutil, "which", which)
        with pytest.raises(PlaybackError, match="No playback command found for mp3"):
            CommandPlayer()._argv_for(AudioFormat.MP3)
