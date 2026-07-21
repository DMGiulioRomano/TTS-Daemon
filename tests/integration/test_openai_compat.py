"""OpenAI-compatible endpoint: POST /v1/audio/speech."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.conftest import make_config
from tts_daemon.api.app import create_app
from tts_daemon.api.openai_compat import resolve_model, resolve_voice
from tts_daemon.core.service import SpeechService


@pytest.fixture()
def client() -> TestClient:
    app = create_app(make_config(openai_compat={"voice_aliases": {"alloy": "high"}}))
    with TestClient(app) as test_client:
        yield test_client


def _wav_ok(response) -> None:
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content[:4] == b"RIFF"


class TestAudioSpeechEndpoint:
    def test_basic_synthesis_returns_wav_bytes(self, client: TestClient) -> None:
        response = client.post(
            "/v1/audio/speech",
            json={"model": "tts-1", "input": "hello openai", "voice": "alloy"},
        )
        _wav_ok(response)

    def test_authorization_header_is_accepted_and_ignored(self, client: TestClient) -> None:
        response = client.post(
            "/v1/audio/speech",
            headers={"Authorization": "Bearer whatever"},
            json={"model": "tts-1", "input": "hi", "voice": "nova"},
        )
        _wav_ok(response)

    def test_explicit_provider_via_model(self, client: TestClient) -> None:
        response = client.post(
            "/v1/audio/speech",
            json={"model": "tone", "input": "beep", "voice": "mid"},
        )
        _wav_ok(response)

    def test_unsupported_response_format_is_422(self, client: TestClient) -> None:
        response = client.post(
            "/v1/audio/speech",
            json={
                "model": "tts-1",
                "input": "hi",
                "voice": "alloy",
                "response_format": "mp3",
            },
        )
        assert response.status_code == 422
        assert "mp3" in response.json()["detail"]

    def test_wav_response_format_is_accepted(self, client: TestClient) -> None:
        response = client.post(
            "/v1/audio/speech",
            json={
                "model": "tts-1",
                "input": "hi",
                "voice": "alloy",
                "response_format": "wav",
            },
        )
        _wav_ok(response)

    def test_empty_input_is_422(self, client: TestClient) -> None:
        response = client.post(
            "/v1/audio/speech",
            json={"model": "tts-1", "input": "", "voice": "alloy"},
        )
        assert response.status_code == 422

    def test_out_of_range_speed_is_422(self, client: TestClient) -> None:
        response = client.post(
            "/v1/audio/speech",
            json={"model": "tts-1", "input": "hi", "voice": "alloy", "speed": 9.0},
        )
        assert response.status_code == 422

    def test_unknown_field_is_rejected(self, client: TestClient) -> None:
        response = client.post(
            "/v1/audio/speech",
            json={"model": "tts-1", "input": "hi", "voice": "alloy", "bogus": 1},
        )
        assert response.status_code == 422


class TestMappingHelpers:
    def test_resolve_model_prefers_registered_provider(self) -> None:
        service = _service()
        try:
            assert resolve_model("tone", service) == "tone"
            assert resolve_model("tts-1", service) is None
            assert resolve_model("tts-1-hd", service) is None
            assert resolve_model("nonexistent-model", service) is None
        finally:
            service.close()

    def test_resolve_voice_alias_wins(self) -> None:
        assert resolve_voice("alloy", {"alloy": "high"}) == "high"

    def test_resolve_voice_openai_name_falls_back_to_default(self) -> None:
        assert resolve_voice("nova", {}) is None

    def test_resolve_voice_passes_through_provider_id(self) -> None:
        assert resolve_voice("en_US-lessac-medium", {}) == "en_US-lessac-medium"


def _service() -> SpeechService:
    from tts_daemon.core.events import EventBus
    from tts_daemon.players.null import NullPlayer
    from tts_daemon.providers.registry import create_default_registry

    config = make_config()
    return SpeechService(config, create_default_registry(config), NullPlayer(), EventBus())
