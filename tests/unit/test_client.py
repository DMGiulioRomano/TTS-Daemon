"""GatewayClient plumbing: the optional bearer token is sent as a header."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from tts_daemon.client import GatewayClient


class _FakeResponse(io.BytesIO):
    """Minimal context-manager stand-in for urlopen's return value."""

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _capture(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """Patch urlopen to record the Request and return an empty JSON object."""
    captured: list[Any] = []

    def fake_urlopen(request: Any, timeout: float | None = None) -> _FakeResponse:
        captured.append(request)
        return _FakeResponse(json.dumps({}).encode("utf-8"))

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return captured


def test_no_token_sends_no_authorization_header(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    GatewayClient("http://gateway.test").status()
    assert not captured[0].has_header("Authorization")


def test_token_is_sent_as_bearer_header(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    GatewayClient("http://gateway.test", token="abc123").status()
    # urllib capitalizes header keys internally ("Authorization").
    assert captured[0].get_header("Authorization") == "Bearer abc123"


def test_empty_token_is_treated_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture(monkeypatch)
    GatewayClient("http://gateway.test", token="").status()
    assert not captured[0].has_header("Authorization")
