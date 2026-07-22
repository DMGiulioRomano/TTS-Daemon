"""Tiny HTTP helper shared by the cloud providers (``openai``, ``elevenlabs``).

Both talk to a REST API over the standard library only — no SDK, no new runtime
dependency (the same ``urllib`` pattern used by ``client.py`` and the Claude
Code hook). This module centralises the one fiddly part: turning ``urllib``'s
exceptions into an actionable :class:`SynthesisError`, including a *truncated*
slice of the API's own error message, and enforcing a per-call timeout so a hung
cloud request can never wedge the playback queue.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import Callable

from tts_daemon.core.errors import SynthesisError

#: How much of an upstream error body to echo back (keep messages readable).
_ERROR_TAIL = 300


def request_bytes(
    url: str,
    *,
    method: str = "GET",
    headers: dict[str, str],
    data: bytes | None = None,
    timeout: float,
    provider: str,
    extract_error: Callable[[bytes], str],
) -> bytes:
    """Perform one HTTP request and return the raw response body.

    ``extract_error`` pulls a human message out of an error response body (the
    JSON shape differs per API). HTTP errors become a ``SynthesisError`` naming
    the status and the (truncated) API message; connection failures and
    timeouts become a ``SynthesisError`` that points at the likely cause.
    """
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        body = _safe_read(exc)
        detail = extract_error(body) or exc.reason or "unknown error"
        raise SynthesisError(
            f"{provider} API request failed (HTTP {exc.code}): {_truncate(detail)}"
        ) from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        # URLError covers DNS/refused/TLS and connect timeouts (its ``reason``
        # may itself be a TimeoutError); a bare socket read timeout surfaces as
        # TimeoutError. Both mean the cloud call did not complete.
        reason = getattr(exc, "reason", exc)
        raise SynthesisError(
            f"{provider} request failed: {reason}. This is a cloud provider — it needs "
            "network access and a valid api_key, and it gives up after the configured "
            "timeout (providers.{}.timeout_seconds).".format(provider)
        ) from exc


def _safe_read(exc: urllib.error.HTTPError) -> bytes:
    try:
        return exc.read()
    except Exception:  # pragma: no cover - body already consumed / unreadable
        return b""


def _truncate(text: str, limit: int = _ERROR_TAIL) -> str:
    text = " ".join(text.split())  # collapse whitespace/newlines for one-line errors
    if len(text) <= limit:
        return text
    return text[:limit] + "…"
