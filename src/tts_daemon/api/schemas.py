"""Pydantic request/response models for the HTTP and WebSocket APIs.

These DTOs are the public wire contract (documented in docs/api.md and the
generated OpenAPI schema). Keep changes backwards-compatible: add optional
fields, never repurpose existing ones.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SpeakRequest(BaseModel):
    """Body of ``POST /v1/speak``."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="Text to speak.", min_length=1)
    provider: str | None = Field(
        default=None,
        description="Provider name; defaults to the configured default (or 'auto').",
    )
    voice: str | None = Field(default=None, description="Provider-specific voice id.")
    speed: float = Field(default=1.0, gt=0, le=10, description="Rate multiplier; 1.0 is normal.")
    options: dict[str, Any] = Field(
        default_factory=dict, description="Provider-specific options passed through untouched."
    )
    interrupt: bool = Field(
        default=False, description="Cancel queued and current speech before this utterance."
    )
    wait: bool = Field(
        default=False,
        description="Do not return until the utterance finished (or failed/was cancelled).",
    )


class SynthesizeRequest(BaseModel):
    """Body of ``POST /v1/synthesize`` (returns audio bytes, plays nothing)."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(description="Text to synthesize.", min_length=1)
    provider: str | None = None
    voice: str | None = None
    speed: float = Field(default=1.0, gt=0, le=10)
    options: dict[str, Any] = Field(default_factory=dict)


class OpenAISpeechRequest(BaseModel):
    """Body of ``POST /v1/audio/speech`` — the OpenAI ``audio.speech`` schema.

    Accepted so any OpenAI TTS client works by pointing its ``base_url`` at the
    gateway; the gateway maps these fields onto its own providers.
    """

    model_config = ConfigDict(extra="forbid")

    model: str = Field(
        description="OpenAI model name (tts-1/tts-1-hd → gateway default) or a "
        "registered provider name (e.g. 'piper') to select it explicitly."
    )
    input: str = Field(description="Text to speak.", min_length=1)
    voice: str = Field(
        description="OpenAI voice name (mapped via openai_compat.voice_aliases, "
        "else the provider default) or a provider voice id passed through."
    )
    response_format: str | None = Field(
        default=None, description="Audio format; only 'wav' is supported for now."
    )
    speed: float = Field(default=1.0, ge=0.25, le=4.0, description="OpenAI speed range.")


class UtteranceModel(BaseModel):
    """Snapshot of an utterance's state."""

    id: str
    text: str
    provider: str
    voice: str | None = None
    speed: float = 1.0
    state: str
    error: str | None = None
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None


class SpeakResponse(BaseModel):
    utterance: UtteranceModel


class StopResponse(BaseModel):
    cancelled: int = Field(description="Utterances cancelled (queued + interrupted).")


class VoiceModel(BaseModel):
    id: str
    name: str
    language: str | None = None
    description: str | None = None
    provider: str


class VoicesResponse(BaseModel):
    voices: list[VoiceModel]


class ProviderModel(BaseModel):
    name: str
    available: bool
    reason: str | None = Field(default=None, description="Why the provider is unavailable.")
    default: bool = Field(description="Whether this provider is the resolved default.")


class ProvidersResponse(BaseModel):
    providers: list[ProviderModel]


class QueueModel(BaseModel):
    current: UtteranceModel | None = None
    queued: list[UtteranceModel]
    history: list[UtteranceModel]
    size: int
    max_size: int


class CacheModel(BaseModel):
    entries: int
    size_mb: float
    hits: int
    misses: int


class StatusResponse(BaseModel):
    queue: QueueModel
    default_provider: str | None
    default_provider_error: str | None = None
    playback_available: bool
    cache: CacheModel | None = Field(
        default=None, description="Synthesis cache stats, or null when the cache is disabled."
    )


class HealthResponse(BaseModel):
    status: str = "ok"
    version: str


class ErrorResponse(BaseModel):
    detail: str
