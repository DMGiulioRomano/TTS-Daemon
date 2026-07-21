"""edge-tts provider — hermetic (the real package is never imported).

A fake ``edge_tts`` module is injected into ``sys.modules`` so speed mapping,
option handling, and MP3 output are tested without network or the dependency.
The "not installed" paths run with no fake at all (the package genuinely isn't
in the test environment).
"""

from __future__ import annotations

import importlib.machinery
import sys
import types
from collections.abc import Iterator

import pytest

from tts_daemon.core.errors import SynthesisError
from tts_daemon.core.models import AudioFormat, SynthesisRequest
from tts_daemon.providers.edge import EdgeProvider, _rate


class _FakeCommunicate:
    last: _FakeCommunicate | None = None

    def __init__(self, text: str, voice: str, **kwargs: str) -> None:
        self.text = text
        self.voice = voice
        self.kwargs = kwargs
        _FakeCommunicate.last = self

    async def stream(self):
        yield {"type": "audio", "data": b"ID3fake"}
        yield {"type": "WordBoundary"}  # non-audio frames are ignored
        yield {"type": "audio", "data": b"-audio"}


def _make_fake_module() -> types.ModuleType:
    module = types.ModuleType("edge_tts")
    module.__spec__ = importlib.machinery.ModuleSpec("edge_tts", loader=None)
    module.Communicate = _FakeCommunicate
    module._voices_calls = 0

    async def list_voices():
        module._voices_calls += 1
        return [
            {"ShortName": "en-US-AriaNeural", "FriendlyName": "Aria", "Locale": "en-US"},
            {"ShortName": "it-IT-ElsaNeural", "FriendlyName": "Elsa", "Locale": "it-IT"},
            {"FriendlyName": "no shortname"},  # skipped
        ]

    module.list_voices = list_voices
    return module


@pytest.fixture()
def fake_edge(monkeypatch: pytest.MonkeyPatch) -> Iterator[types.ModuleType]:
    module = _make_fake_module()
    _FakeCommunicate.last = None
    monkeypatch.setitem(sys.modules, "edge_tts", module)
    yield module


def test_rate_mapping() -> None:
    assert _rate(1.5) == "+50%"
    assert _rate(1.0) == "+0%"
    assert _rate(0.8) == "-20%"


# ----------------------------------------------------------- package installed


def test_synthesize_returns_mp3(fake_edge: types.ModuleType) -> None:
    clip = EdgeProvider().synthesize(SynthesisRequest("hello"))
    assert clip.format is AudioFormat.MP3
    assert clip.data == b"ID3fake-audio"


def test_speed_maps_to_rate(fake_edge: types.ModuleType) -> None:
    EdgeProvider().synthesize(SynthesisRequest("hi", speed=1.5))
    assert _FakeCommunicate.last.kwargs["rate"] == "+50%"


def test_default_and_requested_voice(fake_edge: types.ModuleType) -> None:
    EdgeProvider().synthesize(SynthesisRequest("hi"))
    assert _FakeCommunicate.last.voice == "en-US-AriaNeural"
    EdgeProvider({"default_voice": "de-DE-KatjaNeural"}).synthesize(SynthesisRequest("hi"))
    assert _FakeCommunicate.last.voice == "de-DE-KatjaNeural"
    EdgeProvider().synthesize(SynthesisRequest("hi", voice="it-IT-ElsaNeural"))
    assert _FakeCommunicate.last.voice == "it-IT-ElsaNeural"


def test_volume_and_pitch_options_pass_through(fake_edge: types.ModuleType) -> None:
    EdgeProvider().synthesize(SynthesisRequest("hi", options={"volume": "+10%", "pitch": "-5Hz"}))
    assert _FakeCommunicate.last.kwargs["volume"] == "+10%"
    assert _FakeCommunicate.last.kwargs["pitch"] == "-5Hz"


def test_unknown_option_is_rejected(fake_edge: types.ModuleType) -> None:
    with pytest.raises(SynthesisError, match="Unknown edge options: speaker"):
        EdgeProvider().synthesize(SynthesisRequest("hi", options={"speaker": 1}))


def test_non_positive_speed_rejected(fake_edge: types.ModuleType) -> None:
    with pytest.raises(SynthesisError, match="speed must be positive"):
        EdgeProvider().synthesize(SynthesisRequest("hi", speed=0))


def test_availability_ok_when_present(fake_edge: types.ModuleType) -> None:
    assert EdgeProvider().availability().available is True


def test_voices_listed_and_cached(fake_edge: types.ModuleType) -> None:
    provider = EdgeProvider()
    voices = provider.voices()
    ids = [voice.id for voice in voices]
    assert ids == ["en-US-AriaNeural", "it-IT-ElsaNeural"]  # entry without ShortName skipped
    provider.voices()  # cached -> package not queried again
    assert fake_edge._voices_calls == 1


# --------------------------------------------------------- package NOT installed


def test_availability_missing_gives_install_hint() -> None:
    availability = EdgeProvider().availability()
    assert availability.available is False
    assert "tts-daemon[edge]" in availability.reason


def test_synthesize_missing_raises_actionable() -> None:
    with pytest.raises(SynthesisError, match="tts-daemon\\[edge\\]"):
        EdgeProvider().synthesize(SynthesisRequest("hi"))


def test_voices_missing_returns_empty() -> None:
    assert EdgeProvider().voices() == []
