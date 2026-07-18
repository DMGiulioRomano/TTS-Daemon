"""HTTP API integration tests: real app, real service, tone provider, no sound."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tests.conftest import BlockingProvider, make_config
from tts_gateway.api.app import create_app


@pytest.fixture()
def client() -> TestClient:
    app = create_app(make_config())
    # context manager runs lifespan: service shut down cleanly at exit
    with TestClient(app) as test_client:
        yield test_client


class TestMeta:
    def test_health(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["version"]

    def test_index_page_links_docs(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "/docs" in response.text

    def test_openapi_schema_is_served(self, client: TestClient) -> None:
        assert client.get("/openapi.json").status_code == 200

    def test_cors_headers_for_browser_clients(self, client: TestClient) -> None:
        response = client.get("/health", headers={"Origin": "https://example.com"})
        assert response.headers.get("access-control-allow-origin") == "*"

    def test_providers_listing(self, client: TestClient) -> None:
        response = client.get("/v1/providers")
        assert response.status_code == 200
        providers = {p["name"]: p for p in response.json()["providers"]}
        assert providers["tone"]["available"] is True
        assert providers["tone"]["default"] is True
        assert "piper" in providers  # registered even when unavailable


class TestSpeak:
    def test_speak_returns_202_and_utterance_completes(self, client: TestClient) -> None:
        response = client.post("/v1/speak", json={"text": "hello integration"})
        assert response.status_code == 202
        utterance = response.json()["utterance"]
        assert utterance["state"] in {"queued", "synthesizing", "speaking", "finished"}

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            found = client.get(f"/v1/utterances/{utterance['id']}").json()["utterance"]
            if found["state"] == "finished":
                return
            time.sleep(0.02)
        pytest.fail("utterance never finished")

    def test_speak_wait_returns_200_with_final_state(self, client: TestClient) -> None:
        response = client.post("/v1/speak", json={"text": "wait for me", "wait": True})
        assert response.status_code == 200
        utterance = response.json()["utterance"]
        assert utterance["state"] == "finished"
        assert utterance["error"] is None
        assert utterance["finished_at"] is not None

    def test_speak_with_unknown_provider_is_404(self, client: TestClient) -> None:
        response = client.post("/v1/speak", json={"text": "x", "provider": "imaginary"})
        assert response.status_code == 404
        assert "imaginary" in response.json()["detail"]

    def test_speak_with_unavailable_provider_is_503(self, client: TestClient) -> None:
        # piper is registered but not installed in the test environment
        response = client.post("/v1/speak", json={"text": "x", "provider": "piper"})
        assert response.status_code == 503
        assert "piper" in response.json()["detail"]

    def test_validation_errors_are_422(self, client: TestClient) -> None:
        assert client.post("/v1/speak", json={}).status_code == 422  # no text
        assert client.post("/v1/speak", json={"text": ""}).status_code == 422
        assert client.post("/v1/speak", json={"text": "x", "speed": -1}).status_code == 422
        assert client.post("/v1/speak", json={"text": "x", "bogus_field": 1}).status_code == 422

    def test_text_over_configured_limit_is_422(self) -> None:
        app = create_app(make_config(speech={"max_text_length": 12}))
        with TestClient(app) as client:
            response = client.post("/v1/speak", json={"text": "definitely more than twelve"})
            assert response.status_code == 422
            assert "limit is 12" in response.json()["detail"]

    def test_synthesis_failure_is_502_on_wait(self, client: TestClient) -> None:
        # tone rejects unknown voices at synthesis time -> utterance fails
        response = client.post(
            "/v1/speak", json={"text": "x", "voice": "not-a-voice", "wait": True}
        )
        assert response.status_code == 200  # queued fine; failure is in the state
        utterance = response.json()["utterance"]
        assert utterance["state"] == "failed"
        assert "Unknown tone voice" in utterance["error"]


class TestSynthesize:
    def test_returns_wav_bytes(self, client: TestClient) -> None:
        response = client.post("/v1/synthesize", json={"text": "give me bytes"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "audio/wav"
        assert response.content.startswith(b"RIFF")

    def test_synthesis_error_is_502(self, client: TestClient) -> None:
        response = client.post("/v1/synthesize", json={"text": "x", "voice": "not-a-voice"})
        assert response.status_code == 502
        assert "Unknown tone voice" in response.json()["detail"]


class TestStopAndStatus:
    def test_stop_on_idle_gateway(self, client: TestClient) -> None:
        response = client.post("/v1/stop")
        assert response.status_code == 200
        assert response.json() == {"cancelled": 0}

    def test_status_shape(self, client: TestClient) -> None:
        response = client.get("/v1/status")
        assert response.status_code == 200
        body = response.json()
        assert body["default_provider"] == "tone"
        assert body["playback_available"] is True
        assert body["queue"]["current"] is None
        assert body["queue"]["queued"] == []
        assert body["queue"]["max_size"] == 8

    def test_unknown_utterance_is_404(self, client: TestClient) -> None:
        assert client.get("/v1/utterances/doesnotexist").status_code == 404

    def test_stop_cancels_backlog(self, client: TestClient) -> None:
        # Block the worker inside synthesis so a backlog builds up.
        service = client.app.state.service
        service.registry.register(BlockingProvider)
        blocking = service.registry.get("blocking")

        first = client.post("/v1/speak", json={"text": "block", "provider": "blocking"})
        assert first.status_code == 202
        assert blocking.entered.acquire(timeout=2), "worker never reached synthesis"
        backlog = [client.post("/v1/speak", json={"text": f"b{i}"}) for i in range(3)]
        assert all(response.status_code == 202 for response in backlog)

        response = client.post("/v1/stop")
        assert response.status_code == 200
        assert response.json()["cancelled"] == 4  # 1 in-flight + 3 queued
        blocking.release()  # let the worker unblock and observe the cancel

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            state = client.get(f"/v1/utterances/{first.json()['utterance']['id']}")
            if state.json()["utterance"]["state"] == "cancelled":
                break
            time.sleep(0.02)
        else:
            pytest.fail("blocked utterance was never cancelled")

    def test_queue_full_is_429(self) -> None:
        app = create_app(make_config(speech={"queue_size": 2}))
        with TestClient(app) as client:
            service = client.app.state.service
            service.registry.register(BlockingProvider)
            blocking = service.registry.get("blocking")
            try:
                assert (
                    client.post(
                        "/v1/speak", json={"text": "block", "provider": "blocking"}
                    ).status_code
                    == 202
                )
                assert blocking.entered.acquire(timeout=2)
                for i in range(2):
                    assert client.post("/v1/speak", json={"text": f"fill{i}"}).status_code == 202
                overflow = client.post("/v1/speak", json={"text": "overflow"})
                assert overflow.status_code == 429
                assert "full" in overflow.json()["detail"]
            finally:
                blocking.release()
                client.post("/v1/stop")


class TestVoices:
    def test_all_voices(self, client: TestClient) -> None:
        response = client.get("/v1/voices")
        assert response.status_code == 200
        voices = response.json()["voices"]
        assert {voice["provider"] for voice in voices} == {"tone"}

    def test_provider_filter(self, client: TestClient) -> None:
        response = client.get("/v1/voices", params={"provider": "tone"})
        ids = {voice["id"] for voice in response.json()["voices"]}
        assert ids == {"low", "mid", "high"}

    def test_unknown_provider_is_404(self, client: TestClient) -> None:
        assert client.get("/v1/voices", params={"provider": "nope"}).status_code == 404
