"""ElevenLabsProvider tested with ``urllib`` mocked (fully hermetic).

``urllib.request.urlopen`` is replaced with a fake that records the outgoing
request and returns configured bytes (or raises the error under test), so
request building, the voices API, and error mapping are checked with no socket.
"""

from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from tts_daemon.core.errors import SynthesisError
from tts_daemon.core.models import AudioFormat, SynthesisRequest
from tts_daemon.providers.elevenlabs import ElevenLabsProvider

_RACHEL = "21m00Tcm4TlvDq8ikWAM"


@pytest.fixture()
def fake_http(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Replace ``urllib.request.urlopen`` with a recording fake."""
    state = SimpleNamespace(body=b"ID3fake-mp3-bytes", error=None, requests=[])

    class _Response:
        def __init__(self, body: bytes) -> None:
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *exc: object) -> bool:
            return False

    def fake_urlopen(request: urllib.request.Request, timeout: float | None = None):
        state.requests.append(request)
        if state.error is not None:
            raise state.error
        return _Response(state.body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return state


@pytest.fixture()
def provider(monkeypatch: pytest.MonkeyPatch) -> ElevenLabsProvider:
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    return ElevenLabsProvider({"api_key": "el-test"})


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.elevenlabs.io", code, "err", {}, io.BytesIO(body))


class TestSynthesis:
    def test_returns_mp3_clip(
        self, fake_http: SimpleNamespace, provider: ElevenLabsProvider
    ) -> None:
        clip = provider.synthesize(SynthesisRequest(text="hello"))
        assert clip.format is AudioFormat.MP3
        assert clip.data == fake_http.body
        request = fake_http.requests[-1]
        assert request.full_url.endswith(f"/text-to-speech/{_RACHEL}")
        assert request.method == "POST"
        assert request.headers["Xi-api-key"] == "el-test"
        body = json.loads(request.data)
        assert body["text"] == "hello"
        assert body["model_id"] == "eleven_multilingual_v2"
        assert "voice_settings" not in body

    def test_voice_override(self, fake_http: SimpleNamespace, provider: ElevenLabsProvider) -> None:
        provider.synthesize(SynthesisRequest(text="x", voice="AbC123"))
        assert fake_http.requests[-1].full_url.endswith("/text-to-speech/AbC123")

    def test_model_id_setting(self, fake_http: SimpleNamespace) -> None:
        ElevenLabsProvider({"api_key": "el-test", "model_id": "eleven_turbo_v2"}).synthesize(
            SynthesisRequest(text="x")
        )
        assert json.loads(fake_http.requests[-1].data)["model_id"] == "eleven_turbo_v2"

    def test_voice_settings_passthrough(
        self, fake_http: SimpleNamespace, provider: ElevenLabsProvider
    ) -> None:
        provider.synthesize(
            SynthesisRequest(text="x", options={"stability": 0.3, "similarity_boost": 0.9})
        )
        settings = json.loads(fake_http.requests[-1].data)["voice_settings"]
        assert settings == {"stability": 0.3, "similarity_boost": 0.9}

    def test_non_numeric_option_rejected(
        self, fake_http: SimpleNamespace, provider: ElevenLabsProvider
    ) -> None:
        with pytest.raises(SynthesisError, match="must be a number"):
            provider.synthesize(SynthesisRequest(text="x", options={"stability": "high"}))

    def test_unknown_option_rejected(
        self, fake_http: SimpleNamespace, provider: ElevenLabsProvider
    ) -> None:
        with pytest.raises(SynthesisError, match="Unknown elevenlabs options: emotion"):
            provider.synthesize(SynthesisRequest(text="x", options={"emotion": "sad"}))

    def test_speed_is_ignored_not_an_error(
        self, fake_http: SimpleNamespace, provider: ElevenLabsProvider
    ) -> None:
        provider.synthesize(SynthesisRequest(text="x", speed=2.0))
        body = json.loads(fake_http.requests[-1].data)
        assert "speed" not in body  # ElevenLabs rate control is intentionally not wired

    def test_invalid_speed_rejected(
        self, fake_http: SimpleNamespace, provider: ElevenLabsProvider
    ) -> None:
        with pytest.raises(SynthesisError, match="speed must be positive"):
            provider.synthesize(SynthesisRequest(text="x", speed=0))

    def test_missing_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        with pytest.raises(SynthesisError, match="api_key is not set"):
            ElevenLabsProvider().synthesize(SynthesisRequest(text="x"))

    def test_empty_response_is_an_error(
        self, fake_http: SimpleNamespace, provider: ElevenLabsProvider
    ) -> None:
        fake_http.body = b""
        with pytest.raises(SynthesisError, match="no audio"):
            provider.synthesize(SynthesisRequest(text="x"))


class TestErrorMapping:
    def test_http_error_dict_detail(
        self, fake_http: SimpleNamespace, provider: ElevenLabsProvider
    ) -> None:
        fake_http.error = _http_error(422, b'{"detail": {"message": "voice not found"}}')
        with pytest.raises(SynthesisError, match="HTTP 422") as excinfo:
            provider.synthesize(SynthesisRequest(text="x"))
        assert "voice not found" in str(excinfo.value)

    def test_http_error_string_detail(
        self, fake_http: SimpleNamespace, provider: ElevenLabsProvider
    ) -> None:
        fake_http.error = _http_error(401, b'{"detail": "quota exceeded"}')
        with pytest.raises(SynthesisError, match="quota exceeded"):
            provider.synthesize(SynthesisRequest(text="x"))

    def test_network_error_is_actionable(
        self, fake_http: SimpleNamespace, provider: ElevenLabsProvider
    ) -> None:
        fake_http.error = urllib.error.URLError("name resolution failed")
        with pytest.raises(SynthesisError, match="request failed"):
            provider.synthesize(SynthesisRequest(text="x"))


class TestVoices:
    _PAYLOAD = json.dumps(
        {
            "voices": [
                {
                    "voice_id": "v1",
                    "name": "Rachel",
                    "labels": {"language": "en", "description": "calm"},
                },
                {"voice_id": "v2", "name": "Domi"},
            ]
        }
    ).encode()

    def test_voices_from_api(
        self, fake_http: SimpleNamespace, provider: ElevenLabsProvider
    ) -> None:
        fake_http.body = self._PAYLOAD
        voices = {voice.id: voice for voice in provider.voices()}
        assert set(voices) == {"v1", "v2"}
        assert voices["v1"].name == "Rachel"
        assert voices["v1"].language == "en"
        assert voices["v1"].description == "calm"
        assert fake_http.requests[-1].full_url.endswith("/voices")
        assert fake_http.requests[-1].method == "GET"

    def test_voices_are_cached(
        self, fake_http: SimpleNamespace, provider: ElevenLabsProvider
    ) -> None:
        fake_http.body = self._PAYLOAD
        first = provider.voices()
        second = provider.voices()
        assert first is second
        assert len(fake_http.requests) == 1  # fetched once

    def test_voices_empty_without_key(
        self, fake_http: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        assert ElevenLabsProvider().voices() == []
        assert fake_http.requests == []  # no request attempted without a key

    def test_voices_empty_on_api_failure(
        self, fake_http: SimpleNamespace, provider: ElevenLabsProvider
    ) -> None:
        fake_http.error = _http_error(500, b"boom")
        assert provider.voices() == []


class TestAvailability:
    def test_available_with_key(self, provider: ElevenLabsProvider) -> None:
        assert provider.availability().available

    def test_unavailable_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
        availability = ElevenLabsProvider().availability()
        assert not availability.available
        assert "ELEVENLABS_API_KEY" in availability.reason

    def test_api_key_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ELEVENLABS_API_KEY", "el-from-env")
        assert ElevenLabsProvider().availability().available
