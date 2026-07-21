"""OpenAI-compatible speech endpoint: ``POST /v1/audio/speech``.

OpenAI API compatibility is what let Ollama plug into every existing client by
just changing ``base_url``. The TTS equivalent: any app built against OpenAI's
speech endpoint gets free, private, local voices by pointing at this gateway::

    from openai import OpenAI
    client = OpenAI(base_url="http://127.0.0.1:5111/v1", api_key="unused")
    client.audio.speech.create(model="tts-1", input="Hello", voice="alloy")

The route is a thin translation layer over ``SpeechService.synthesize`` — no
core changes. The ``Authorization`` header is accepted and ignored (until
bearer auth lands, issue #18).
"""

from __future__ import annotations

import anyio.to_thread
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from tts_daemon.api.schemas import ErrorResponse, OpenAISpeechRequest
from tts_daemon.config import GatewayConfig
from tts_daemon.core.models import AudioFormat
from tts_daemon.core.service import SpeechService

router = APIRouter()

#: The standard OpenAI voice names. An unaliased one falls back to the
#: provider's default voice; any other string is treated as a provider voice id.
_OPENAI_VOICES = frozenset(
    {"alloy", "ash", "ballad", "coral", "echo", "fable", "onyx", "nova", "sage", "shimmer", "verse"}
)

#: OpenAI "model" names that mean "just use the gateway default provider".
_GENERIC_MODELS = frozenset({"tts-1", "tts-1-hd", "gpt-4o-mini-tts"})

#: Response formats we can currently return (providers emit WAV today).
_SUPPORTED_FORMATS = frozenset({"wav"})


@router.post(
    "/audio/speech",
    summary="OpenAI-compatible speech synthesis (returns audio bytes)",
    tags=["speech"],
    responses={
        200: {"content": {"audio/wav": {}}, "description": "Synthesized audio bytes"},
        404: {"model": ErrorResponse, "description": "Unknown provider"},
        422: {"model": ErrorResponse, "description": "Invalid request / unsupported format"},
        502: {"model": ErrorResponse, "description": "Synthesis failed"},
        503: {"model": ErrorResponse, "description": "Provider unavailable"},
    },
)
async def audio_speech(body: OpenAISpeechRequest, request: Request) -> Response:
    """Translate an OpenAI ``audio.speech`` request and return audio bytes."""
    service: SpeechService = request.app.state.service
    config: GatewayConfig = request.app.state.config

    if body.response_format is not None and body.response_format not in _SUPPORTED_FORMATS:
        return JSONResponse(
            status_code=422,
            content={
                "detail": f"response_format {body.response_format!r} is not supported yet; "
                f"only {sorted(_SUPPORTED_FORMATS)} is available"
            },
        )

    provider = resolve_model(body.model, service)
    voice = resolve_voice(body.voice, config.openai_compat.voice_aliases)

    clip = await anyio.to_thread.run_sync(
        lambda: service.synthesize(
            body.input,
            provider=provider,
            voice=voice,
            speed=body.speed,
        )
    )

    if body.response_format is not None and clip.format is not AudioFormat(body.response_format):
        return JSONResponse(
            status_code=422,
            content={
                "detail": f"provider produced {clip.format.value!r} audio but "
                f"response_format {body.response_format!r} was requested"
            },
        )
    return Response(content=clip.data, media_type=clip.media_type)


def resolve_model(model: str, service: SpeechService) -> str | None:
    """Map the OpenAI ``model`` field onto a gateway provider.

    A registered provider name selects it explicitly; the generic OpenAI model
    names (and anything unrecognized) resolve to the gateway default provider.
    """
    if model in service.registry:
        return model
    if model in _GENERIC_MODELS:
        return None
    # Unknown model: be lenient like OpenAI clients expect and use the default.
    return None


def resolve_voice(voice: str, aliases: dict[str, str]) -> str | None:
    """Map the OpenAI ``voice`` field onto a provider voice id.

    A configured alias wins; an unaliased standard OpenAI voice falls back to
    the provider default (``None``); anything else is passed through as a
    provider voice id.
    """
    if voice in aliases:
        return aliases[voice]
    if voice in _OPENAI_VOICES:
        return None
    return voice
