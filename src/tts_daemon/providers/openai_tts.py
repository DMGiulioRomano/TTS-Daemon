"""OpenAI TTS provider (cloud, opt-in) — premium voices with no SDK.

Calls OpenAI's ``POST /audio/speech`` endpoint over the standard library only
(the same ``urllib`` pattern as ``client.py``), so enabling it adds **zero**
runtime dependencies and needs no extra. It is opt-in: nothing happens until you
provide an API key.

**Trade-offs (be honest with users):** this is a *cloud* provider — the text you
synthesize is sent to OpenAI, and you are billed per character. Prefer piper or
kokoro for anything private or offline. See ``docs/providers.md`` and
``docs/configuration.md``.

Settings (``providers.openai`` in the config file):

``api_key``
    OpenAI API key. Falls back to the ``OPENAI_API_KEY`` environment variable.
``model``
    Model id (default ``gpt-4o-mini-tts``; also ``tts-1``, ``tts-1-hd``).
``default_voice``
    Voice used when a request names none (default ``alloy``).
``base_url``
    API base (default ``https://api.openai.com/v1``); override for a proxy or an
    Azure/OpenAI-compatible endpoint.
``timeout_seconds``
    Per-request timeout (default 30) so a hung call cannot wedge the queue.

``request.speed`` is forwarded as OpenAI's ``speed`` parameter, but only when it
differs from ``1.0``: ``gpt-4o-mini-tts`` rejects an explicit ``speed``, so the
default model keeps working out of the box while ``tts-1``/``tts-1-hd`` still get
rate control when asked.
"""

from __future__ import annotations

import json
import os

from tts_daemon.core.errors import SynthesisError
from tts_daemon.core.interfaces import TTSProvider
from tts_daemon.core.models import AudioClip, AudioFormat, Availability, SynthesisRequest, Voice
from tts_daemon.providers import _http

_DEFAULT_MODEL = "gpt-4o-mini-tts"
_DEFAULT_VOICE = "alloy"
_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_DEFAULT_TIMEOUT = 30.0

# Static catalog (OpenAI does not expose a voice-listing endpoint).
_VOICES = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "nova",
    "onyx",
    "sage",
    "shimmer",
    "verse",
)


class OpenAITTSProvider(TTSProvider):
    """Synthesize speech via OpenAI's cloud TTS API (stdlib ``urllib`` only)."""

    name = "openai"

    def __init__(self, settings: dict | None = None) -> None:
        super().__init__(settings)
        self._api_key = str(self.settings.get("api_key") or os.environ.get("OPENAI_API_KEY") or "")
        self._model = str(self.settings.get("model") or _DEFAULT_MODEL)
        self._default_voice = str(self.settings.get("default_voice") or _DEFAULT_VOICE)
        self._base_url = str(self.settings.get("base_url") or _DEFAULT_BASE_URL).rstrip("/")
        self._timeout = float(self.settings.get("timeout_seconds") or _DEFAULT_TIMEOUT)

    # ------------------------------------------------------------------ api

    def synthesize(self, request: SynthesisRequest) -> AudioClip:
        if request.speed <= 0:
            raise SynthesisError(f"speed must be positive, got {request.speed}")
        if request.options:
            unknown = ", ".join(sorted(request.options))
            raise SynthesisError(f"The openai provider accepts no options (got: {unknown})")
        if not self._api_key:
            raise SynthesisError(
                "openai api_key is not set (providers.openai.api_key or $OPENAI_API_KEY)"
            )

        body: dict[str, object] = {
            "model": self._model,
            "voice": request.voice or self._default_voice,
            "input": request.text,
            "response_format": "mp3",
        }
        # gpt-4o-mini-tts refuses an explicit speed; only send it when non-default
        # so the default model works untouched and tts-1/tts-1-hd still get it.
        if request.speed != 1.0:
            body["speed"] = request.speed

        data = _http.request_bytes(
            f"{self._base_url}/audio/speech",
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            data=json.dumps(body).encode("utf-8"),
            timeout=self._timeout,
            provider="openai",
            extract_error=_extract_error,
        )
        if not data:
            raise SynthesisError("openai returned an empty audio response")
        return AudioClip(data=data, format=AudioFormat.MP3)

    def voices(self) -> list[Voice]:
        # A fixed catalog: OpenAI has no voice-listing endpoint, and these do not
        # depend on the key, so they are always visible in /v1/voices.
        return [Voice(id=name, name=name.capitalize(), language="en") for name in _VOICES]

    def availability(self) -> Availability:
        # Local check only (no network): is there a key to authenticate with?
        if not self._api_key:
            return Availability.unavailable(
                "set providers.openai.api_key or $OPENAI_API_KEY (openai is a paid cloud provider)"
            )
        return Availability.ok()


def _extract_error(body: bytes) -> str:
    """Pull the message out of OpenAI's ``{"error": {"message": ...}}`` body."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body.decode("utf-8", "replace")
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return str(error.get("message") or "")
    if isinstance(error, str):
        return error
    return ""
