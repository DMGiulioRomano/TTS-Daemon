"""OpenAITTSProvider tested with ``urllib`` mocked (fully hermetic).

No test touches the network: ``urllib.request.urlopen`` is replaced with a fake
that records the outgoing request and returns configured bytes (or raises the
``urllib`` error under test), so request building and error mapping are checked
without a socket ever opening.
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
from tts_daemon.providers.openai_tts import OpenAITTSProvider


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
        state.timeout = timeout
        if state.error is not None:
            raise state.error
        return _Response(state.body)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return state


@pytest.fixture()
def provider(monkeypatch: pytest.MonkeyPatch) -> OpenAITTSProvider:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    return OpenAITTSProvider({"api_key": "sk-test"})


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.openai.com", code, "err", {}, io.BytesIO(body))


class TestSynthesis:
    def test_returns_mp3_clip(
        self, fake_http: SimpleNamespace, provider: OpenAITTSProvider
    ) -> None:
        clip = provider.synthesize(SynthesisRequest(text="hello"))
        assert clip.format is AudioFormat.MP3
        assert clip.data == fake_http.body
        request = fake_http.requests[-1]
        assert request.full_url.endswith("/audio/speech")
        assert request.method == "POST"
        assert request.headers["Authorization"] == "Bearer sk-test"
        body = json.loads(request.data)
        assert body["model"] == "gpt-4o-mini-tts"
        assert body["voice"] == "alloy"  # default
        assert body["input"] == "hello"
        assert body["response_format"] == "mp3"
        assert "speed" not in body  # default speed is not sent

    def test_voice_override(self, fake_http: SimpleNamespace, provider: OpenAITTSProvider) -> None:
        provider.synthesize(SynthesisRequest(text="x", voice="nova"))
        assert json.loads(fake_http.requests[-1].data)["voice"] == "nova"

    def test_model_setting(self, fake_http: SimpleNamespace) -> None:
        OpenAITTSProvider({"api_key": "sk-test", "model": "tts-1-hd"}).synthesize(
            SynthesisRequest(text="x")
        )
        assert json.loads(fake_http.requests[-1].data)["model"] == "tts-1-hd"

    def test_speed_sent_only_when_non_default(
        self, fake_http: SimpleNamespace, provider: OpenAITTSProvider
    ) -> None:
        provider.synthesize(SynthesisRequest(text="x", speed=1.5))
        assert json.loads(fake_http.requests[-1].data)["speed"] == 1.5

    def test_options_are_rejected(
        self, fake_http: SimpleNamespace, provider: OpenAITTSProvider
    ) -> None:
        with pytest.raises(SynthesisError, match="accepts no options"):
            provider.synthesize(SynthesisRequest(text="x", options={"pitch": 2}))

    def test_invalid_speed_rejected(
        self, fake_http: SimpleNamespace, provider: OpenAITTSProvider
    ) -> None:
        with pytest.raises(SynthesisError, match="speed must be positive"):
            provider.synthesize(SynthesisRequest(text="x", speed=0))

    def test_missing_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        with pytest.raises(SynthesisError, match="api_key is not set"):
            OpenAITTSProvider().synthesize(SynthesisRequest(text="x"))

    def test_empty_response_is_an_error(
        self, fake_http: SimpleNamespace, provider: OpenAITTSProvider
    ) -> None:
        fake_http.body = b""
        with pytest.raises(SynthesisError, match="empty audio"):
            provider.synthesize(SynthesisRequest(text="x"))


class TestErrorMapping:
    def test_http_error_includes_api_message(
        self, fake_http: SimpleNamespace, provider: OpenAITTSProvider
    ) -> None:
        fake_http.error = _http_error(401, b'{"error": {"message": "Invalid API key"}}')
        with pytest.raises(SynthesisError, match="HTTP 401") as excinfo:
            provider.synthesize(SynthesisRequest(text="x"))
        assert "Invalid API key" in str(excinfo.value)

    def test_network_error_is_actionable(
        self, fake_http: SimpleNamespace, provider: OpenAITTSProvider
    ) -> None:
        fake_http.error = urllib.error.URLError("Connection refused")
        with pytest.raises(SynthesisError, match="request failed") as excinfo:
            provider.synthesize(SynthesisRequest(text="x"))
        assert "Connection refused" in str(excinfo.value)

    def test_timeout_is_mapped(
        self, fake_http: SimpleNamespace, provider: OpenAITTSProvider
    ) -> None:
        fake_http.error = TimeoutError("timed out")
        with pytest.raises(SynthesisError, match="request failed"):
            provider.synthesize(SynthesisRequest(text="x"))


class TestVoices:
    def test_static_voice_list(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        ids = {voice.id for voice in OpenAITTSProvider().voices()}  # no key needed
        assert {"alloy", "echo", "fable", "onyx", "nova", "shimmer"} <= ids


class TestAvailability:
    def test_available_with_key(self, provider: OpenAITTSProvider) -> None:
        assert provider.availability().available

    def test_unavailable_without_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        availability = OpenAITTSProvider().availability()
        assert not availability.available
        assert "OPENAI_API_KEY" in availability.reason

    def test_api_key_from_env(
        self, fake_http: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
        provider = OpenAITTSProvider()
        assert provider.availability().available
        provider.synthesize(SynthesisRequest(text="x"))
        assert fake_http.requests[-1].headers["Authorization"] == "Bearer sk-from-env"


class TestMp3PlaybackRouting:
    """The clip is MP3, so the player must route it to an MP3-capable command."""

    def test_command_player_routes_mp3(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tts_daemon.players.command as command_module
        from tts_daemon.players.command import CommandPlayer

        def which(name: str) -> str | None:
            return "/usr/bin/ffplay" if name == "ffplay" else None

        monkeypatch.setattr(command_module.shutil, "which", which)
        assert CommandPlayer()._argv_for(AudioFormat.MP3)[0] == "ffplay"
