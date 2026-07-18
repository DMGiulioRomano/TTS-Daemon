"""ToneProvider: the dependency-free reference provider."""

from __future__ import annotations

import pytest

from tts_gateway.core.errors import SynthesisError
from tts_gateway.core.models import AudioFormat, SynthesisRequest
from tts_gateway.providers.tone import MAX_BEEPS, ToneProvider


@pytest.fixture()
def provider() -> ToneProvider:
    return ToneProvider({})


def test_produces_valid_wav(provider: ToneProvider) -> None:
    clip = provider.synthesize(SynthesisRequest(text="hello world"))
    assert clip.format is AudioFormat.WAV
    assert clip.data.startswith(b"RIFF")
    assert clip.duration_seconds is not None
    assert clip.duration_seconds > 0.1


def test_duration_grows_with_word_count(provider: ToneProvider) -> None:
    short = provider.synthesize(SynthesisRequest(text="one"))
    long = provider.synthesize(SynthesisRequest(text="one two three four five"))
    assert long.duration_seconds > short.duration_seconds


def test_speed_shortens_audio(provider: ToneProvider) -> None:
    normal = provider.synthesize(SynthesisRequest(text="some words here"))
    fast = provider.synthesize(SynthesisRequest(text="some words here", speed=2.0))
    assert fast.duration_seconds < normal.duration_seconds


def test_is_always_available(provider: ToneProvider) -> None:
    assert provider.availability().available


def test_voices_listing(provider: ToneProvider) -> None:
    ids = [voice.id for voice in provider.voices()]
    assert ids == ["high", "low", "mid"]


def test_voice_selection_changes_output(provider: ToneProvider) -> None:
    low = provider.synthesize(SynthesisRequest(text="beep", voice="low"))
    high = provider.synthesize(SynthesisRequest(text="beep", voice="high"))
    assert low.data != high.data


def test_default_voice_setting_is_used(provider: ToneProvider) -> None:
    configured = ToneProvider({"default_voice": "high"})
    explicit = provider.synthesize(SynthesisRequest(text="beep", voice="high"))
    assert configured.synthesize(SynthesisRequest(text="beep")).data == explicit.data


def test_unknown_voice_is_rejected(provider: ToneProvider) -> None:
    with pytest.raises(SynthesisError, match="Unknown tone voice 'basso'"):
        provider.synthesize(SynthesisRequest(text="x", voice="basso"))


def test_options_are_rejected(provider: ToneProvider) -> None:
    with pytest.raises(SynthesisError, match="no options"):
        provider.synthesize(SynthesisRequest(text="x", options={"pitch": 2}))


def test_invalid_speed_is_rejected(provider: ToneProvider) -> None:
    with pytest.raises(SynthesisError, match="speed"):
        provider.synthesize(SynthesisRequest(text="x", speed=0))


def test_whitespace_only_text_still_beeps(provider: ToneProvider) -> None:
    clip = provider.synthesize(SynthesisRequest(text="   "))
    assert clip.duration_seconds > 0


def test_huge_text_is_capped(provider: ToneProvider) -> None:
    capped = provider.synthesize(SynthesisRequest(text="word " * (MAX_BEEPS + 500)))
    exact = provider.synthesize(SynthesisRequest(text="word " * MAX_BEEPS))
    assert capped.duration_seconds == exact.duration_seconds
