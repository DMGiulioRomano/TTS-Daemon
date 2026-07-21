"""Optional bearer-token authentication for the ``/v1`` API.

When ``server.auth_token`` is set, every ``/v1`` route requires the token,
supplied either as an ``Authorization: Bearer <token>`` header or — because
browsers cannot set headers on ``WebSocket``/``EventSource`` connections — as a
``?token=`` query parameter. ``GET /health`` and the playground at ``/`` stay
open so liveness checks and the token prompt keep working.

The token is compared with :func:`secrets.compare_digest` (constant time), and
the scheme is wired as a FastAPI dependency (not middleware) so ``/health`` and
``/`` are naturally excluded and the OpenAPI schema documents the requirement.
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable

from fastapi import Depends, HTTPException, Request, WebSocket
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

#: Loopback hosts for which running without a token is unremarkable.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "::ffff:127.0.0.1"})

#: 401 body; the standard ``{"detail": ...}`` shape used across the API.
_UNAUTHORIZED_DETAIL = (
    "Missing or invalid bearer token. Send 'Authorization: Bearer <token>' "
    "(or ?token=<token> for WebSocket/SSE)."
)

#: Documents the scheme in OpenAPI without auto-raising, so the query-param
#: fallback can still run. ``auto_error=False`` -> returns None on absence.
_bearer_scheme = HTTPBearer(
    auto_error=False,
    description="Bearer token from server.auth_token (when authentication is enabled).",
)

#: Module-level ``Depends`` marker (a singleton, so it is not a function call in
#: a default argument — see ruff B008). Using ``Depends`` here is what makes
#: FastAPI document the bearer scheme in the OpenAPI schema.
_BEARER_DEPENDENCY = Depends(_bearer_scheme)


def is_loopback(host: str) -> bool:
    """Whether ``host`` only accepts connections from the local machine."""
    normalized = host.strip().lower()
    return normalized in _LOOPBACK_HOSTS or normalized.startswith("127.")


def _extract_query_token(value: str | None) -> str | None:
    return value or None


def build_auth_dependency(
    expected_token: str | None,
) -> Callable[..., Awaitable[None]] | None:
    """A FastAPI dependency enforcing ``expected_token``.

    Returns ``None`` when authentication is disabled (``expected_token`` is
    falsy) so the caller can attach no dependency at all — keeping the OpenAPI
    schema free of a security scheme that is not actually in effect.
    """
    if not expected_token:
        return None

    async def verify(
        request: Request,
        credentials: HTTPAuthorizationCredentials | None = _BEARER_DEPENDENCY,
    ) -> None:
        provided = (
            credentials.credentials
            if credentials is not None
            else _extract_query_token(request.query_params.get("token"))
        )
        if not provided or not secrets.compare_digest(provided, expected_token):
            raise HTTPException(
                status_code=401,
                detail=_UNAUTHORIZED_DETAIL,
                headers={"WWW-Authenticate": "Bearer"},
            )

    return verify


def _token_from_headers(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() != "bearer":
        return None
    return credentials.strip() or None


def websocket_authorized(websocket: WebSocket, expected_token: str | None) -> bool:
    """Whether a WebSocket handshake carries the required token (if any).

    Checks the ``Authorization`` header first, then the ``?token=`` query
    parameter (browsers cannot set headers on ``WebSocket``).
    """
    if not expected_token:
        return True
    provided = _token_from_headers(websocket.headers.get("Authorization")) or _extract_query_token(
        websocket.query_params.get("token")
    )
    return bool(provided) and secrets.compare_digest(provided, expected_token)
