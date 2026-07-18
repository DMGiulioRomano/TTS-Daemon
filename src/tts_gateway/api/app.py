"""FastAPI application factory.

``create_app`` wires the object graph (config -> registry -> player ->
service) and installs the single place where domain errors are translated to
HTTP status codes. Each call builds an isolated app, which keeps tests and
embedding straightforward.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse

from tts_gateway import __version__
from tts_gateway.api import http, websocket
from tts_gateway.api.schemas import HealthResponse
from tts_gateway.config import GatewayConfig, load_config
from tts_gateway.core.errors import (
    ConfigError,
    GatewayError,
    PlaybackError,
    ProviderUnavailableError,
    QueueFullError,
    SynthesisError,
    UnknownProviderError,
)
from tts_gateway.core.events import EventBus
from tts_gateway.core.service import SpeechService
from tts_gateway.players import create_player
from tts_gateway.providers.registry import create_default_registry

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
        title="tts-gateway",
        version=__version__,
        description=(
            "Local text-to-speech gateway: send text over HTTP or WebSocket, "
            "hear it through interchangeable TTS providers."
        ),
        lifespan=lifespan,
    )
    app.state.config = config
    app.state.service = service

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

    app.include_router(http.router, prefix="/v1")
    app.include_router(websocket.router)

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    async def index() -> str:
        return _INDEX_HTML

    return app


_INDEX_HTML = f"""\
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>tts-gateway</title>
<style>
  body {{ font: 16px/1.6 system-ui, sans-serif; max-width: 42rem;
         margin: 3rem auto; padding: 0 1rem; }}
  code {{ background: #8881; padding: .1em .35em; border-radius: 4px; }}
</style>
</head>
<body>
<h1>tts-gateway <small>v{__version__}</small></h1>
<p>A local text-to-speech gateway. Interactive API docs: <a href="/docs">/docs</a></p>
<p>Try it:</p>
<pre><code>curl -X POST localhost:5111/v1/speak \\
  -H 'content-type: application/json' \\
  -d '{{"text": "Hello from the gateway"}}'</code></pre>
<p>Endpoints: <code>POST /v1/speak</code>, <code>POST /v1/stop</code>,
<code>POST /v1/synthesize</code>, <code>GET /v1/status</code>,
<code>GET /v1/voices</code>, <code>GET /v1/providers</code>,
<code>WS /v1/ws</code>, <code>GET /health</code></p>
</body>
</html>
"""
