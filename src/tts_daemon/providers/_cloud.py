"""Tiny stdlib HTTP helper shared by the cloud providers (OpenAI, ElevenLabs).

No SDKs: cloud providers are one ``urllib`` call each. This module centralises
that call and the error extraction so a failed request always yields a
:class:`CloudError` carrying the HTTP status and the upstream error message
(truncated), which the provider maps onto a ``SynthesisError``.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_ERROR_TAIL = 300  # cap the upstream error text we echo back


class CloudError(Exception):
    """An HTTP or network failure talking to a cloud TTS API."""

    def __init__(self, status: int | None, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(f"HTTP {status}: {message}" if status else message)


def post_bytes(url: str, *, headers: dict[str, str], data: bytes, timeout: float) -> bytes:
    """POST ``data`` and return the raw response body."""
    return _request(url, headers=headers, data=data, timeout=timeout)


def get_json(url: str, *, headers: dict[str, str], timeout: float) -> Any:
    """GET ``url`` and parse the JSON response body."""
    raw = _request(url, headers=headers, data=None, timeout=timeout)
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CloudError(None, f"invalid JSON response: {exc}") from exc


def _request(url: str, *, headers: dict[str, str], data: bytes | None, timeout: float) -> bytes:
    request = Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.read()
    except HTTPError as exc:
        raise CloudError(exc.code, _error_message(exc)) from exc
    except URLError as exc:
        raise CloudError(None, f"network error: {exc.reason}") from exc


def _error_message(exc: HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", "replace")
    except OSError:
        return str(exc.reason) or "request failed"
    try:
        parsed = json.loads(body)
    except ValueError:
        return body[:_ERROR_TAIL].strip() or (str(exc.reason) or "request failed")
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:_ERROR_TAIL]
        detail = parsed.get("detail")
        if isinstance(detail, dict) and detail.get("message"):
            return str(detail["message"])[:_ERROR_TAIL]
        if isinstance(detail, str):
            return detail[:_ERROR_TAIL]
    return body[:_ERROR_TAIL].strip()
