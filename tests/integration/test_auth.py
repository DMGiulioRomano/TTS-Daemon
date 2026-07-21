"""Optional bearer-token authentication (server.auth_token)."""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.conftest import make_config
from tts_daemon.api.app import create_app
from tts_daemon.api.auth import is_loopback

TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def open_client() -> TestClient:
    """A gateway with no auth_token (the default)."""
    with TestClient(create_app(make_config())) as client:
        yield client


@pytest.fixture()
def auth_client() -> TestClient:
    """A gateway that requires the bearer token."""
    with TestClient(create_app(make_config(server={"auth_token": TOKEN}))) as client:
        yield client


class TestDisabledByDefault:
    def test_v1_open_without_token(self, open_client: TestClient) -> None:
        assert open_client.get("/v1/status").status_code == 200
        assert open_client.get("/v1/providers").status_code == 200

    def test_openapi_has_no_security_scheme_when_disabled(self, open_client: TestClient) -> None:
        schema = open_client.get("/openapi.json").json()
        assert "securitySchemes" not in schema.get("components", {})


class TestRestAuth:
    def test_missing_token_is_401(self, auth_client: TestClient) -> None:
        response = auth_client.get("/v1/status")
        assert response.status_code == 401
        assert "detail" in response.json()  # standard error shape

    def test_wrong_token_is_401(self, auth_client: TestClient) -> None:
        response = auth_client.get("/v1/status", headers={"Authorization": "Bearer nope"})
        assert response.status_code == 401

    def test_non_bearer_scheme_is_401(self, auth_client: TestClient) -> None:
        response = auth_client.get("/v1/status", headers={"Authorization": f"Basic {TOKEN}"})
        assert response.status_code == 401

    def test_correct_token_passes(self, auth_client: TestClient) -> None:
        assert auth_client.get("/v1/status", headers=AUTH).status_code == 200

    def test_query_param_token_passes(self, auth_client: TestClient) -> None:
        # Browsers can't set headers on EventSource/WebSocket, so ?token= works.
        assert auth_client.get("/v1/status", params={"token": TOKEN}).status_code == 200
        assert auth_client.get("/v1/status", params={"token": "nope"}).status_code == 401

    def test_post_speak_requires_token(self, auth_client: TestClient) -> None:
        assert auth_client.post("/v1/speak", json={"text": "hi"}).status_code == 401
        ok = auth_client.post("/v1/speak", json={"text": "hi"}, headers=AUTH)
        assert ok.status_code == 202

    def test_openai_compat_endpoint_is_guarded(self, auth_client: TestClient) -> None:
        body = {"model": "tts-1", "input": "hello", "voice": "alloy"}
        assert auth_client.post("/v1/audio/speech", json=body).status_code == 401
        ok = auth_client.post("/v1/audio/speech", json=body, headers=AUTH)
        assert ok.status_code == 200


class TestOpenEndpointsStayOpen:
    def test_health_open(self, auth_client: TestClient) -> None:
        assert auth_client.get("/health").status_code == 200

    def test_index_open(self, auth_client: TestClient) -> None:
        assert auth_client.get("/").status_code == 200

    def test_openapi_documents_scheme(self, auth_client: TestClient) -> None:
        schema = auth_client.get("/openapi.json").json()
        assert "HTTPBearer" in schema["components"]["securitySchemes"]


class TestSseAuth:
    # The SSE stream itself is unbounded (see tests/integration/test_sse.py), but
    # a 401 is returned *before* the stream opens, so TestClient can read it.
    def test_sse_missing_token_is_401(self, auth_client: TestClient) -> None:
        assert auth_client.get("/v1/events").status_code == 401

    def test_sse_wrong_query_token_is_401(self, auth_client: TestClient) -> None:
        assert auth_client.get("/v1/events", params={"token": "nope"}).status_code == 401


class TestWebSocketAuth:
    def test_ws_open_without_auth(self, open_client: TestClient) -> None:
        with open_client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "ping", "id": 1})
            assert ws.receive_json()["type"] == "pong"

    def test_ws_rejected_without_token(self, auth_client: TestClient) -> None:
        with pytest.raises(WebSocketDisconnect):
            with auth_client.websocket_connect("/v1/ws") as ws:
                ws.receive_json()

    def test_ws_rejected_with_wrong_query_token(self, auth_client: TestClient) -> None:
        with pytest.raises(WebSocketDisconnect):
            with auth_client.websocket_connect("/v1/ws", params={"token": "nope"}) as ws:
                ws.receive_json()

    def test_ws_accepts_query_token(self, auth_client: TestClient) -> None:
        with auth_client.websocket_connect("/v1/ws", params={"token": TOKEN}) as ws:
            ws.send_json({"type": "ping", "id": 7})
            message = ws.receive_json()
            assert message["type"] == "pong"
            assert message["id"] == 7

    def test_ws_accepts_header_token(self, auth_client: TestClient) -> None:
        with auth_client.websocket_connect("/v1/ws", headers=AUTH) as ws:
            ws.send_json({"type": "ping", "id": 8})
            assert ws.receive_json()["type"] == "pong"


class TestStartupWarning:
    def test_warns_when_public_without_token(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="tts_daemon.api.app"):
            create_app(make_config(server={"host": "0.0.0.0"}))
        assert any("auth_token" in record.message for record in caplog.records)

    def test_no_warning_on_loopback(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="tts_daemon.api.app"):
            create_app(make_config(server={"host": "127.0.0.1"}))
        assert not any("auth_token" in record.message for record in caplog.records)

    def test_no_warning_when_public_but_token_set(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger="tts_daemon.api.app"):
            create_app(make_config(server={"host": "0.0.0.0", "auth_token": TOKEN}))
        assert not any("auth_token" in record.message for record in caplog.records)


class TestIsLoopback:
    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "127.5.5.5"])
    def test_loopback_hosts(self, host: str) -> None:
        assert is_loopback(host)

    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "::"])
    def test_public_hosts(self, host: str) -> None:
        assert not is_loopback(host)
