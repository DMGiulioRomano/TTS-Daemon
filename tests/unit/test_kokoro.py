"""KokoroProvider tested with the kokoro-onnx package mocked (fully hermetic).

The real package loads an ONNX model from disk; here a fake ``kokoro_onnx``
module is injected into ``sys.modules`` and the model/voices files are tiny
temp files, so nothing heavy is loaded and nothing touches the network. Tests
that assert the "not installed" behaviour simply omit the fixture — the extra
is not part of the dev install.
"""

from __future__ import annotations

import io
import sys
import types
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from tts_daemon.core.errors import SynthesisError
from tts_daemon.core.models import AudioFormat, SynthesisRequest
from tts_daemon.providers.kokoro import KokoroProvider


@pytest.fixture()
def model_files(tmp_path: Path) -> SimpleNamespace:
    """A pair of throwaway model/voices files so ``is_file()`` checks pass."""
    model = tmp_path / "kokoro-v1.0.onnx"
    voices = tmp_path / "voices-v1.0.bin"
    model.write_bytes(b"fake-onnx-model")
    voices.write_bytes(b"fake-voices")
    return SimpleNamespace(model=model, voices=voices)


@pytest.fixture()
def fake_kokoro(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Install a configurable fake ``kokoro_onnx`` module; return its state."""
    state = SimpleNamespace(
        samples=[0.0, 0.5, -0.5, 1.0, -1.0, 0.25],
        sample_rate=24000,
        voices=["af_sarah", "am_adam", "bf_emma"],
        calls=[],
        instances=0,
        create_error=None,
    )

    class Kokoro:
        def __init__(self, model_path: str, voices_path: str) -> None:
            state.instances += 1
            self.model_path = model_path
            self.voices_path = voices_path

        def create(self, text, voice, speed=1.0, lang="en-us", **kwargs):
            state.calls.append(SimpleNamespace(text=text, voice=voice, speed=speed, lang=lang))
            if state.create_error is not None:
                raise state.create_error
            return state.samples, state.sample_rate

        def get_voices(self):
            return state.voices

    module = types.ModuleType("kokoro_onnx")
    module.Kokoro = Kokoro
    monkeypatch.setitem(sys.modules, "kokoro_onnx", module)
    return state


@pytest.fixture()
def make_provider(model_files: SimpleNamespace):
    def _make(**settings: object) -> KokoroProvider:
        config: dict[str, object] = {
            "model_path": str(model_files.model),
            "voices_path": str(model_files.voices),
        }
        config.update(settings)
        return KokoroProvider(config)

    return _make


class TestSynthesis:
    def test_returns_wav_clip(self, fake_kokoro: SimpleNamespace, make_provider) -> None:
        clip = make_provider().synthesize(SynthesisRequest(text="hello"))
        assert clip.format is AudioFormat.WAV
        assert clip.data.startswith(b"RIFF")
        with wave.open(io.BytesIO(clip.data)) as wav:
            assert wav.getframerate() == fake_kokoro.sample_rate
            assert wav.getnframes() == len(fake_kokoro.samples)
        call = fake_kokoro.calls[-1]
        assert call.text == "hello"
        assert call.voice == "af_sarah"  # default voice
        assert call.speed == 1.0
        assert call.lang == "en-us"

    def test_voice_override(self, fake_kokoro: SimpleNamespace, make_provider) -> None:
        make_provider().synthesize(SynthesisRequest(text="x", voice="am_adam"))
        assert fake_kokoro.calls[-1].voice == "am_adam"

    def test_default_voice_setting(self, fake_kokoro: SimpleNamespace, make_provider) -> None:
        make_provider(default_voice="bf_emma").synthesize(SynthesisRequest(text="x"))
        assert fake_kokoro.calls[-1].voice == "bf_emma"

    def test_speed_passthrough(self, fake_kokoro: SimpleNamespace, make_provider) -> None:
        make_provider().synthesize(SynthesisRequest(text="x", speed=1.5))
        assert fake_kokoro.calls[-1].speed == 1.5

    def test_lang_option_overrides_default(
        self, fake_kokoro: SimpleNamespace, make_provider
    ) -> None:
        make_provider().synthesize(SynthesisRequest(text="x", options={"lang": "it"}))
        assert fake_kokoro.calls[-1].lang == "it"

    def test_lang_setting_default(self, fake_kokoro: SimpleNamespace, make_provider) -> None:
        make_provider(lang="fr-fr").synthesize(SynthesisRequest(text="x"))
        assert fake_kokoro.calls[-1].lang == "fr-fr"

    def test_unknown_option_rejected(self, fake_kokoro: SimpleNamespace, make_provider) -> None:
        with pytest.raises(SynthesisError, match="Unknown kokoro options: emotion"):
            make_provider().synthesize(SynthesisRequest(text="x", options={"emotion": "sad"}))

    def test_invalid_speed_rejected(self, fake_kokoro: SimpleNamespace, make_provider) -> None:
        with pytest.raises(SynthesisError, match="speed must be positive"):
            make_provider().synthesize(SynthesisRequest(text="x", speed=0))

    def test_empty_samples_is_an_error(self, fake_kokoro: SimpleNamespace, make_provider) -> None:
        fake_kokoro.samples = []
        with pytest.raises(SynthesisError, match="produced no audio"):
            make_provider().synthesize(SynthesisRequest(text="x", voice="af_sarah"))

    def test_create_error_becomes_actionable_synthesis_error(
        self, fake_kokoro: SimpleNamespace, make_provider
    ) -> None:
        fake_kokoro.create_error = RuntimeError("bad voice tensor")
        with pytest.raises(SynthesisError, match="kokoro synthesis failed") as excinfo:
            make_provider().synthesize(SynthesisRequest(text="x"))
        assert "bad voice tensor" in str(excinfo.value)

    def test_engine_is_loaded_once(self, fake_kokoro: SimpleNamespace, make_provider) -> None:
        provider = make_provider()
        provider.synthesize(SynthesisRequest(text="one"))
        provider.synthesize(SynthesisRequest(text="two"))
        assert fake_kokoro.instances == 1  # ONNX session built once, then cached


class TestVoices:
    def test_lists_voice_names(self, fake_kokoro: SimpleNamespace, make_provider) -> None:
        ids = {voice.id for voice in make_provider().voices()}
        assert ids == {"af_sarah", "am_adam", "bf_emma"}

    def test_voices_empty_when_model_missing(
        self, fake_kokoro: SimpleNamespace, model_files: SimpleNamespace
    ) -> None:
        provider = KokoroProvider({"model_path": str(model_files.model.parent / "gone.onnx")})
        assert provider.voices() == []


class TestAvailability:
    def test_available_when_ready(self, fake_kokoro: SimpleNamespace, make_provider) -> None:
        assert make_provider().availability().available

    def test_model_missing_is_reported(
        self, fake_kokoro: SimpleNamespace, model_files: SimpleNamespace
    ) -> None:
        provider = KokoroProvider(
            {
                "model_path": str(model_files.model.parent / "nope.onnx"),
                "voices_path": str(model_files.voices),
            }
        )
        availability = provider.availability()
        assert not availability.available
        assert "model not found" in availability.reason
        assert "kokoro-v1.0.onnx" in availability.reason

    def test_voices_file_missing_is_reported(
        self, fake_kokoro: SimpleNamespace, model_files: SimpleNamespace
    ) -> None:
        provider = KokoroProvider(
            {
                "model_path": str(model_files.model),
                "voices_path": str(model_files.voices.parent / "nope.bin"),
            }
        )
        availability = provider.availability()
        assert not availability.available
        assert "voices file not found" in availability.reason
        assert "voices-v1.0.bin" in availability.reason


class TestFingerprint:
    def test_fingerprint_tracks_model(
        self, fake_kokoro: SimpleNamespace, make_provider, model_files: SimpleNamespace
    ) -> None:
        fingerprint = make_provider().synthesis_fingerprint(SynthesisRequest(text="x"))
        assert fingerprint.startswith("kokoro:")
        assert str(model_files.model) in fingerprint

    def test_fingerprint_falls_back_when_model_absent(self) -> None:
        provider = KokoroProvider({"model_path": "/nonexistent/kokoro.onnx"})
        assert provider.synthesis_fingerprint(SynthesisRequest(text="x")) == "kokoro"


class TestWithoutPackage:
    @pytest.fixture(autouse=True)
    def _hide_kokoro(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Make ``import kokoro_onnx`` fail even if the [kokoro] extra is installed."""
        monkeypatch.setitem(sys.modules, "kokoro_onnx", None)

    def test_availability_reports_missing_package(self) -> None:
        availability = KokoroProvider().availability()
        assert not availability.available
        assert "tts-daemon[kokoro]" in availability.reason

    def test_voices_empty_without_package(self) -> None:
        assert KokoroProvider().voices() == []

    def test_synthesize_without_package_raises(self) -> None:
        with pytest.raises(SynthesisError, match="kokoro-onnx is not installed"):
            KokoroProvider().synthesize(SynthesisRequest(text="x"))
