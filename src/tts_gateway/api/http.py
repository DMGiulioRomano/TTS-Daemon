"""REST routes under ``/v1``.

Routes stay thin: parse the DTO, call the service (off the event loop when
the call can block), shape the response. All error-to-status translation
lives in the app-level exception handlers.
"""

from __future__ import annotations

from typing import Any

import anyio.to_thread
from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse

from tts_gateway.api.schemas import (
    ErrorResponse,
    ProvidersResponse,
    SpeakRequest,
    SpeakResponse,
    StatusResponse,
    StopResponse,
    SynthesizeRequest,
    VoicesResponse,
)
from tts_gateway.core.service import SpeechService

router = APIRouter()

_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    404: {"model": ErrorResponse, "description": "Unknown provider"},
    422: {"model": ErrorResponse, "description": "Invalid request"},
    429: {"model": ErrorResponse, "description": "Queue full"},
    502: {"model": ErrorResponse, "description": "Synthesis failed"},
    503: {"model": ErrorResponse, "description": "Provider unavailable"},
}


def get_service(request: Request) -> SpeechService:
    return request.app.state.service


@router.post(
    "/speak",
    response_model=SpeakResponse,
    responses=_ERROR_RESPONSES,
    status_code=202,
    summary="Queue text for playback",
    tags=["speech"],
)
async def speak(body: SpeakRequest, request: Request) -> Response:
    """Queue an utterance. Returns 202 immediately, or 200 with the final
    state when ``wait`` is true."""
    service = get_service(request)
    utterance = await anyio.to_thread.run_sync(
        lambda: service.speak(
            body.text,
            provider=body.provider,
            voice=body.voice,
            speed=body.speed,
            options=body.options,
            interrupt=body.interrupt,
        )
    )
    status_code = 202
    if body.wait:
        await anyio.to_thread.run_sync(utterance.wait)
        status_code = 200
    return JSONResponse(status_code=status_code, content={"utterance": utterance.snapshot()})


@router.post(
    "/synthesize",
    responses={
        200: {"content": {"audio/wav": {}}, "description": "Synthesized audio bytes"},
        **_ERROR_RESPONSES,
    },
    summary="Synthesize audio and return it (no playback)",
    tags=["speech"],
)
async def synthesize(body: SynthesizeRequest, request: Request) -> Response:
    """Return the audio for ``text`` instead of playing it, so clients
    (browsers, editors) can handle playback themselves."""
    service = get_service(request)
    clip = await anyio.to_thread.run_sync(
        lambda: service.synthesize(
            body.text,
            provider=body.provider,
            voice=body.voice,
            speed=body.speed,
            options=body.options,
        )
    )
    return Response(content=clip.data, media_type=clip.media_type)


@router.post(
    "/stop",
    response_model=StopResponse,
    summary="Stop playback and clear the queue",
    tags=["speech"],
)
async def stop(request: Request) -> StopResponse:
    service = get_service(request)
    cancelled = await anyio.to_thread.run_sync(service.stop)
    return StopResponse(cancelled=cancelled)


@router.get(
    "/status",
    response_model=StatusResponse,
    summary="Queue contents, current utterance, and recent history",
    tags=["meta"],
)
async def status(request: Request) -> StatusResponse:
    return StatusResponse.model_validate(get_service(request).status())


@router.get(
    "/utterances/{utterance_id}",
    responses={404: {"model": ErrorResponse, "description": "Unknown utterance"}},
    summary="State of one utterance (live or recent)",
    tags=["speech"],
)
async def get_utterance(utterance_id: str, request: Request) -> Response:
    snapshot = get_service(request).find_utterance(utterance_id)
    if snapshot is None:
        return JSONResponse(
            status_code=404,
            content={"detail": f"Unknown utterance: {utterance_id!r} (ids expire from history)"},
        )
    return JSONResponse(content={"utterance": snapshot})


@router.get(
    "/voices",
    response_model=VoicesResponse,
    responses={404: {"model": ErrorResponse, "description": "Unknown provider"}},
    summary="Voices, optionally for a single provider",
    tags=["speech"],
)
async def voices(
    request: Request,
    provider: str | None = Query(default=None, description="Restrict to one provider."),
) -> VoicesResponse:
    listed = await anyio.to_thread.run_sync(lambda: get_service(request).voices(provider))
    return VoicesResponse.model_validate({"voices": listed})


@router.get(
    "/providers",
    response_model=ProvidersResponse,
    summary="Registered providers and their availability",
    tags=["meta"],
)
async def providers(request: Request) -> ProvidersResponse:
    info = await anyio.to_thread.run_sync(get_service(request).providers_info)
    return ProvidersResponse.model_validate({"providers": info})
