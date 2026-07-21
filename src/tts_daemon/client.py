"""A tiny HTTP client for a running gateway, built on the standard library.

Used by the CLI and handy for scripts; it needs nothing beyond Python
itself, so ``tts-daemon speak "hi"`` works in any environment that can
reach the server -- including ones where the server's dependencies are not
installed.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from tts_daemon.defaults import DEFAULT_BASE_URL


class GatewayClientError(Exception):
    """The server rejected a request or could not be reached."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GatewayClient:
    """Minimal synchronous client for the gateway's REST API."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = 300.0,
        *,
        token: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # Sent as "Authorization: Bearer <token>" when the gateway requires it
        # (server.auth_token). Left off entirely when unset.
        self.token = token or None

    # ------------------------------------------------------------- commands

    def speak(
        self,
        text: str,
        *,
        provider: str | None = None,
        voice: str | None = None,
        speed: float | None = None,
        interrupt: bool = False,
        wait: bool = False,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"text": text, "interrupt": interrupt, "wait": wait}
        if provider:
            body["provider"] = provider
        if voice:
            body["voice"] = voice
        if speed is not None:
            body["speed"] = speed
        if options:
            body["options"] = options
        return self._request("POST", "/v1/speak", body)

    def synthesize(
        self,
        text: str,
        *,
        provider: str | None = None,
        voice: str | None = None,
        speed: float | None = None,
        options: dict[str, Any] | None = None,
    ) -> bytes:
        body: dict[str, Any] = {"text": text}
        if provider:
            body["provider"] = provider
        if voice:
            body["voice"] = voice
        if speed is not None:
            body["speed"] = speed
        if options:
            body["options"] = options
        return self._request_raw("POST", "/v1/synthesize", body)

    def stop(self) -> dict[str, Any]:
        return self._request("POST", "/v1/stop", {})

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/v1/status")

    def voices(self, provider: str | None = None) -> dict[str, Any]:
        path = "/v1/voices"
        if provider:
            path += f"?provider={urllib.parse.quote(provider)}"
        return self._request("GET", path)

    def providers(self) -> dict[str, Any]:
        return self._request("GET", "/v1/providers")

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    # ------------------------------------------------------------- plumbing

    def _request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        raw = self._request_raw(method, path, body)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise GatewayClientError(f"Server returned invalid JSON for {path}: {exc}") from exc

    def _request_raw(self, method: str, path: str, body: dict[str, Any] | None = None) -> bytes:
        url = self.base_url + path
        data = None
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            detail = _error_detail(exc)
            raise GatewayClientError(
                f"{method} {path} failed with HTTP {exc.code}: {detail}", status=exc.code
            ) from exc
        except urllib.error.URLError as exc:
            raise GatewayClientError(
                f"Cannot reach the gateway at {self.base_url}: {exc.reason}. "
                "Is it running? Start it with: tts-daemon serve"
            ) from exc


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except Exception:
        return exc.reason or "unknown error"
    detail = payload.get("detail", payload)
    if isinstance(detail, str):
        return detail
    return json.dumps(detail)
