"""WebSocket integration tests: commands, correlation ids, and event streaming."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from tests.conftest import make_config
from tts_gateway.api.app import create_app


@pytest.fixture()
def client() -> TestClient:
    with TestClient(create_app(make_config())) as test_client:
        yield test_client


def receive_until(ws: WebSocketTestSession, predicate, limit: int = 50) -> dict[str, Any]:
    """Read frames until one matches (events interleave with results)."""
    for _ in range(limit):
        message = ws.receive_json()
        if predicate(message):
            return message
    raise AssertionError("expected message never arrived")


class TestCommands:
    def test_ping_pong_with_correlation_id(self, client: TestClient) -> None:
        with client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "ping", "id": 42})
            message = receive_until(ws, lambda m: m["type"] == "pong")
            assert message["id"] == 42

    def test_status_command(self, client: TestClient) -> None:
        with client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "status", "id": "s1"})
            message = receive_until(ws, lambda m: m["type"] == "result")
            assert message["id"] == "s1"
            assert message["request"] == "status"
            assert message["data"]["default_provider"] == "tone"

    def test_stop_command(self, client: TestClient) -> None:
        with client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "stop"})
            message = receive_until(ws, lambda m: m["type"] == "result")
            assert message["data"] == {"cancelled": 0}

    def test_unknown_command_is_an_error(self, client: TestClient) -> None:
        with client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "shout", "id": 7})
            message = receive_until(ws, lambda m: m["type"] == "error")
            assert message["id"] == 7
            assert "shout" in message["detail"]

    def test_non_object_message_is_an_error(self, client: TestClient) -> None:
        with client.websocket_connect("/v1/ws") as ws:
            ws.send_json(["not", "an", "object"])
            message = receive_until(ws, lambda m: m["type"] == "error")
            assert "JSON object" in message["detail"]


class TestSpeakOverWebSocket:
    def test_speak_result_then_lifecycle_events(self, client: TestClient) -> None:
        with client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "speak", "text": "spoken over websocket", "id": "req-1"})

            result = receive_until(ws, lambda m: m["type"] == "result")
            assert result["id"] == "req-1"
            utterance_id = result["data"]["utterance"]["id"]

            finished = receive_until(
                ws,
                lambda m: (
                    m["type"] == "event"
                    and m["event"]["type"] == "utterance.finished"
                    and m["event"]["data"]["id"] == utterance_id
                ),
            )
            assert finished["event"]["data"]["state"] == "finished"

    def test_speak_validation_error(self, client: TestClient) -> None:
        with client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "speak", "id": 1})  # no text
            message = receive_until(ws, lambda m: m["type"] == "error")
            assert message["id"] == 1
            assert "text" in message["detail"]

    def test_speak_unknown_provider_error(self, client: TestClient) -> None:
        with client.websocket_connect("/v1/ws") as ws:
            ws.send_json({"type": "speak", "text": "x", "provider": "imaginary"})
            message = receive_until(ws, lambda m: m["type"] == "error")
            assert "imaginary" in message["detail"]

    def test_events_reach_a_passive_listener(self, client: TestClient) -> None:
        with client.websocket_connect("/v1/ws") as listener:
            response = client.post("/v1/speak", json={"text": "listened to"})
            assert response.status_code == 202
            utterance_id = response.json()["utterance"]["id"]
            finished = receive_until(
                listener,
                lambda m: (
                    m["type"] == "event"
                    and m["event"]["type"] == "utterance.finished"
                    and m["event"]["data"]["id"] == utterance_id
                ),
            )
            assert finished["event"]["data"]["text"] == "listened to"
