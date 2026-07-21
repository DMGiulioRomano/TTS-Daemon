"""OpenAI TTS provider (cloud, opt-in) — premium voices, no SDK.

Uses OpenAI's ``/audio/speech`` API over stdlib ``urllib`` (no new runtime
dependency). Requires an API key. Note that text leaves your machine and OpenAI
bills per character — see ``docs/providers.md``.

Settings (``providers.openai``):

``api_key``
    OpenAI key (or the ``OPENAI_API_KEY`` environment variable).
``model``
    ``gpt-4o-mini-tts`` (default), ``tts-1``, or ``tts-1-hd``.
``default_voice``
    Voice when a request names none (default ``alloy``).
``timeout_seconds``
    Per-request timeout (default 30).

Per-request ``options``:

``instructions``
    Style guidance for ``gpt-4o-mini-tts`` (ignored by the ``tts-1`` models).
"""

from __future__ import annotations

import json
import os

from tts_daemon.core.errors import SynthesisError
from tts_daemon.core.interfaces import TTSProvider
from tts_daemon.core.models import AudioClip, AudioFormat, Availability, SynthesisRequest, Voice
from tts_daemon.providers import _cloud

# OpenAI's speed parameter is limited to this range.
_MIN_SPEED, _MAX_SPEED = 0.25, 4.0
_VOICES = (
    "alloy",
    "ash",
    "ballad",
    "coral",
    "echo",
    "fable",
    "onyx",
    "nova",
    "sage",
    "shimmer",
    "verse",
)


class OpenAITTSProvider(TTSProvider):
    """Synthesize speech with OpenAI's text-to-speech API."""

    name = "openai"

    def __init__(self, settings: dict | None = None) -> None:
        super().__init__(settings)
        self._api_key = self.settings.get("api_key") or os.environ.get("OPENAI_API_KEY")
        self._model = str(self.settings.get("model") or "gpt-4o-mini-tts")
        self._default_voice = str(self.settings.get("default_voice") or "alloy")
        self._timeout = float(self.settings.get("timeout_seconds") or 30)
        self._base_url = str(self.settings.get("base_url") or "https://api.openai.com/v1")

    def synthesize(self, request: SynthesisRequest) -> AudioClip:
        if not self._api_key:
            raise SynthesisError(
                "OpenAI api_key not set (providers.openai.api_key or $OPENAI_API_KEY)"
            )
        options = dict(request.options)
        instructions = options.pop("instructions", None)
        if options:
            unknown = ", ".join(sorted(options))
            raise SynthesisError(f"Unknown openai options: {unknown} (supported: instructions)")

        payload: dict[str, object] = {
            "model": self._model,
            "input": request.text,
            "voice": request.voice or self._default_voice,
            "response_format": "mp3",
            "speed": min(_MAX_SPEED, max(_MIN_SPEED, request.speed)),
        }
        if instructions is not None:
            payload["instructions"] = str(instructions)

        try:
            data = _cloud.post_bytes(
                f"{self._base_url}/audio/speech",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(payload).encode("utf-8"),
                timeout=self._timeout,
            )
        except _cloud.CloudError as exc:
            raise SynthesisError(f"OpenAI TTS request failed ({exc})") from exc
        if not data:
            raise SynthesisError("OpenAI TTS returned an empty response")
        return AudioClip(data=data, format=AudioFormat.MP3)

    def voices(self) -> list[Voice]:
        # Only advertise the (static) voice list when the provider is usable,
        # so an unconfigured provider doesn't pollute GET /v1/voices.
        if not self._api_key:
            return []
        return [Voice(id=name, name=f"OpenAI {name}") for name in _VOICES]

    def availability(self) -> Availability:
        if not self._api_key:
            return Availability.unavailable(
                "set providers.openai.api_key or the $OPENAI_API_KEY environment variable"
            )
        return Availability.ok()
