"""OpenAI TTS and ElevenLabs providers — hermetic (the HTTP layer is mocked).

Both providers make a single ``urllib`` call through ``providers._cloud``; the
tests replace that module's functions so nothing touches the network. They
assert request shaping, option handling, availability reasons, and error
mapping.
"""

from __future__ import annotations

import json

import pytest

from tts_daemon.core.errors import SynthesisError
from tts_daemon.core.models import AudioFormat, SynthesisRequest
from tts_daemon.providers import _cloud
from tts_daemon.providers.elevenlabs import ElevenLabsProvider
from tts_daemon.providers.openai_tts import OpenAITTSProvider


@pytest.fixture(autouse=True)
def _no_ambient_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)


def _record_post(monkeypatch: pytest.MonkeyPatch, result: bytes = b"AUDIODATA") -> list[dict]:
    calls: list[dict] = []

    def fake_post(url, *, headers, data, timeout):
        calls.append(
            {"url": url, "headers": headers, "json": json.loads(data.decode()), "timeout": timeout}
        )
        return result

    monkeypatch.setattr(_cloud, "post_bytes", fake_post)
    return calls


# --------------------------------------------------------------------- OpenAI


def test_openai_unavailable_without_key() -> None:
    availability = OpenAITTSProvider().availability()
    assert availability.available is False
    assert "OPENAI_API_KEY" in availability.reason


def test_openai_reads_key_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    assert OpenAITTSProvider().availability().available is True


def test_openai_synthesize_shapes_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_post(monkeypatch)
    provider = OpenAITTSProvider({"api_key": "sk-test", "model": "tts-1-hd"})
    clip = provider.synthesize(SynthesisRequest("hello", voice="nova", speed=1.5))
    assert clip.format is AudioFormat.MP3
    assert clip.data == b"AUDIODATA"
    call = calls[0]
    assert call["url"].endswith("/audio/speech")
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert call["json"] == {
        "model": "tts-1-hd",
        "input": "hello",
        "voice": "nova",
        "response_format": "mp3",
        "speed": 1.5,
    }


def test_openai_clamps_speed_to_api_range(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_post(monkeypatch)
    provider = OpenAITTSProvider({"api_key": "sk"})
    provider.synthesize(SynthesisRequest("x", speed=9.0))
    provider.synthesize(SynthesisRequest("x", speed=0.1))
    assert calls[0]["json"]["speed"] == 4.0
    assert calls[1]["json"]["speed"] == 0.25


def test_openai_instructions_option_and_rejection(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_post(monkeypatch)
    provider = OpenAITTSProvider({"api_key": "sk"})
    provider.synthesize(SynthesisRequest("x", options={"instructions": "cheerful"}))
    assert calls[0]["json"]["instructions"] == "cheerful"
    with pytest.raises(SynthesisError, match="Unknown openai options: pitch"):
        provider.synthesize(SynthesisRequest("x", options={"pitch": 1}))


def test_openai_maps_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise _cloud.CloudError(401, "invalid api key")

    monkeypatch.setattr(_cloud, "post_bytes", boom)
    with pytest.raises(SynthesisError, match=r"401.*invalid api key"):
        OpenAITTSProvider({"api_key": "sk"}).synthesize(SynthesisRequest("x"))


def test_openai_voices_are_static_when_keyed() -> None:
    ids = [voice.id for voice in OpenAITTSProvider({"api_key": "sk"}).voices()]
    assert "alloy" in ids and "nova" in ids


def test_openai_voices_empty_without_key() -> None:
    assert OpenAITTSProvider().voices() == []


# ----------------------------------------------------------------- ElevenLabs


def test_elevenlabs_unavailable_without_key() -> None:
    availability = ElevenLabsProvider().availability()
    assert availability.available is False
    assert "ELEVENLABS_API_KEY" in availability.reason


def test_elevenlabs_synthesize_shapes_request(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _record_post(monkeypatch)
    provider = ElevenLabsProvider({"api_key": "el-key", "model_id": "eleven_turbo_v2"})
    clip = provider.synthesize(
        SynthesisRequest("ciao", voice="VOICEID", options={"stability": 0.4})
    )
    assert clip.format is AudioFormat.MP3
    call = calls[0]
    assert call["url"].endswith("/text-to-speech/VOICEID")
    assert call["headers"]["xi-api-key"] == "el-key"
    assert call["json"]["text"] == "ciao"
    assert call["json"]["model_id"] == "eleven_turbo_v2"
    assert call["json"]["voice_settings"] == {"stability": 0.4}


def test_elevenlabs_rejects_unknown_option(monkeypatch: pytest.MonkeyPatch) -> None:
    _record_post(monkeypatch)
    with pytest.raises(SynthesisError, match="Unknown elevenlabs options: speed"):
        ElevenLabsProvider({"api_key": "k"}).synthesize(SynthesisRequest("x", options={"speed": 2}))


def test_elevenlabs_maps_http_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args, **kwargs):
        raise _cloud.CloudError(422, "voice not found")

    monkeypatch.setattr(_cloud, "post_bytes", boom)
    with pytest.raises(SynthesisError, match="voice not found"):
        ElevenLabsProvider({"api_key": "k"}).synthesize(SynthesisRequest("x"))


def test_elevenlabs_voices_without_key_is_empty() -> None:
    assert ElevenLabsProvider().voices() == []


def test_elevenlabs_voices_listed_and_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"n": 0}

    def fake_get(url, *, headers, timeout):
        calls["n"] += 1
        return {
            "voices": [
                {"voice_id": "v1", "name": "Rachel", "labels": {"language": "en"}},
                {"name": "no id"},  # skipped
            ]
        }

    monkeypatch.setattr(_cloud, "get_json", fake_get)
    provider = ElevenLabsProvider({"api_key": "k"})
    voices = provider.voices()
    assert [v.id for v in voices] == ["v1"]
    assert voices[0].language == "en"
    provider.voices()  # cached
    assert calls["n"] == 1
