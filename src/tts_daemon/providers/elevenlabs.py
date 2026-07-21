"""ElevenLabs provider (cloud, opt-in) — premium voices, no SDK.

Uses the ElevenLabs ``/text-to-speech`` API over stdlib ``urllib``. Requires an
API key; text leaves your machine and ElevenLabs bills per character (see
``docs/providers.md``). This model's rate is fixed, so ``speed`` is ignored.

Settings (``providers.elevenlabs``):

``api_key``
    ElevenLabs key (or the ``ELEVENLABS_API_KEY`` environment variable).
``model_id``
    Model id (default ``eleven_multilingual_v2``).
``default_voice``
    Voice id when a request names none.
``timeout_seconds``
    Per-request timeout (default 30).

Per-request ``options``:

``stability`` / ``similarity_boost``
    Passed through to the API's ``voice_settings``.
"""

from __future__ import annotations

import json
import logging
import os

from tts_daemon.core.errors import SynthesisError
from tts_daemon.core.interfaces import TTSProvider
from tts_daemon.core.models import AudioClip, AudioFormat, Availability, SynthesisRequest, Voice
from tts_daemon.providers import _cloud

logger = logging.getLogger(__name__)

# "Rachel" — a default public voice, overridable via default_voice.
_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"
_VOICE_SETTING_KEYS = ("stability", "similarity_boost")


class ElevenLabsProvider(TTSProvider):
    """Synthesize speech with the ElevenLabs API."""

    name = "elevenlabs"

    def __init__(self, settings: dict | None = None) -> None:
        super().__init__(settings)
        self._api_key = self.settings.get("api_key") or os.environ.get("ELEVENLABS_API_KEY")
        self._model_id = str(self.settings.get("model_id") or "eleven_multilingual_v2")
        self._default_voice = str(self.settings.get("default_voice") or _DEFAULT_VOICE)
        self._timeout = float(self.settings.get("timeout_seconds") or 30)
        self._base_url = str(self.settings.get("base_url") or "https://api.elevenlabs.io/v1")
        self._voices_cache: list[Voice] | None = None

    def synthesize(self, request: SynthesisRequest) -> AudioClip:
        if not self._api_key:
            raise SynthesisError(
                "ElevenLabs api_key not set (providers.elevenlabs.api_key or $ELEVENLABS_API_KEY)"
            )
        options = dict(request.options)
        voice_settings = {key: options.pop(key) for key in _VOICE_SETTING_KEYS if key in options}
        if options:
            unknown = ", ".join(sorted(options))
            raise SynthesisError(
                f"Unknown elevenlabs options: {unknown} (supported: stability, similarity_boost)"
            )

        payload: dict[str, object] = {"text": request.text, "model_id": self._model_id}
        if voice_settings:
            payload["voice_settings"] = voice_settings
        voice = request.voice or self._default_voice

        try:
            data = _cloud.post_bytes(
                f"{self._base_url}/text-to-speech/{voice}",
                headers={
                    "xi-api-key": self._api_key,
                    "Content-Type": "application/json",
                    "Accept": "audio/mpeg",
                },
                data=json.dumps(payload).encode("utf-8"),
                timeout=self._timeout,
            )
        except _cloud.CloudError as exc:
            raise SynthesisError(f"ElevenLabs request failed ({exc})") from exc
        if not data:
            raise SynthesisError("ElevenLabs returned an empty response")
        return AudioClip(data=data, format=AudioFormat.MP3)

    def voices(self) -> list[Voice]:
        if not self._api_key:
            logger.info("ElevenLabs api_key not set; cannot list voices")
            return []
        if self._voices_cache is None:
            try:
                raw = _cloud.get_json(
                    f"{self._base_url}/voices",
                    headers={"xi-api-key": self._api_key},
                    timeout=self._timeout,
                )
            except _cloud.CloudError:
                logger.exception("Listing ElevenLabs voices failed")
                return []
            entries = raw.get("voices", []) if isinstance(raw, dict) else []
            self._voices_cache = [
                Voice(
                    id=entry.get("voice_id", ""),
                    name=entry.get("name") or entry.get("voice_id", ""),
                    language=(entry.get("labels") or {}).get("language"),
                )
                for entry in entries
                if entry.get("voice_id")
            ]
        return self._voices_cache

    def availability(self) -> Availability:
        if not self._api_key:
            return Availability.unavailable(
                "set providers.elevenlabs.api_key or the $ELEVENLABS_API_KEY environment variable"
            )
        return Availability.ok()
