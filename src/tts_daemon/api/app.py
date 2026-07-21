"""FastAPI application factory.

``create_app`` wires the object graph (config -> registry -> player ->
service) and installs the single place where domain errors are translated to
HTTP status codes. Each call builds an isolated app, which keeps tests and
embedding straightforward.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from importlib.resources import files

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from tts_daemon import __version__
from tts_daemon.api import http, openai_compat, websocket
from tts_daemon.api.auth import build_auth_dependency, is_loopback
from tts_daemon.api.schemas import HealthResponse
from tts_daemon.config import GatewayConfig, load_config
from tts_daemon.core.errors import (
    ConfigError,
    GatewayError,
    PlaybackError,
    ProviderUnavailableError,
    QueueFullError,
    SynthesisError,
    UnknownProviderError,
)
from tts_daemon.core.events import EventBus
from tts_daemon.core.service import SpeechService
from tts_daemon.players import create_player
from tts_daemon.providers.registry import create_default_registry

logger = logging.getLogger(__name__)

#: One place that decides how each domain error surfaces over HTTP.
ERROR_STATUS_CODES: tuple[tuple[type[Exception], int], ...] = (
    (UnknownProviderError, 404),
    (ProviderUnavailableError, 503),
    (QueueFullError, 429),
    (SynthesisError, 502),  # the upstream engine failed: literally a bad gateway
    (PlaybackError, 500),
    (ConfigError, 500),
    (GatewayError, 500),
    (ValueError, 422),
)


def status_code_for(exc: Exception) -> int:
    for exc_type, status in ERROR_STATUS_CODES:
        if isinstance(exc, exc_type):
            return status
    return 500


def create_app(config: GatewayConfig | None = None) -> FastAPI:
    """Build the gateway application (loading configuration if not given)."""
    config = config or load_config()

    events = EventBus()
    registry = create_default_registry(config)
    player = create_player(config.playback)
    service = SpeechService(config, registry, player, events)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            service.close()

    app = FastAPI(
        title="tts-daemon",
        version=__version__,
        description=(
            "Local text-to-speech gateway: send text over HTTP or WebSocket, "
            "hear it through interchangeable TTS providers."
        ),
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.service = service

    if config.server.auth_token is None and not is_loopback(config.server.host):
        logger.warning(
            "server.host is %r (not loopback) but server.auth_token is not set: "
            "the gateway is reachable without authentication. Set server.auth_token "
            "(or the TTS_DAEMON__SERVER__AUTH_TOKEN env var) to require a bearer token.",
            config.server.host,
        )

    if config.server.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.server.cors_origins,
            allow_credentials=False,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(GatewayError)
    async def handle_gateway_error(_: Request, exc: GatewayError) -> JSONResponse:
        return JSONResponse(status_code=status_code_for(exc), content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def handle_value_error(_: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=status_code_for(exc), content={"detail": str(exc)})

    auth_dependency = build_auth_dependency(config.server.auth_token)
    v1_dependencies = [Depends(auth_dependency)] if auth_dependency is not None else []
    app.include_router(http.router, prefix="/v1", dependencies=v1_dependencies)
    app.include_router(openai_compat.router, prefix="/v1", dependencies=v1_dependencies)
    app.include_router(websocket.router)

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> str:
        return _playground_html()

    return app


def _playground_html() -> str:
    """The interactive playground page, read from packaged static data (cached)."""
    global _INDEX_HTML
    if _INDEX_HTML is None:
        _INDEX_HTML = (files("tts_daemon.api") / "static" / "index.html").read_text(
            encoding="utf-8"
        )
    return _INDEX_HTML


#: Lazily-loaded, then cached playground markup.
_INDEX_HTML: str | None = None
