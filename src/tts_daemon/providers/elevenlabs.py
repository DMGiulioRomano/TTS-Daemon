"""ElevenLabs TTS provider (cloud, opt-in) — premium voices with no SDK.

Calls the ElevenLabs REST API over the standard library only (the same
``urllib`` pattern as ``client.py``), so enabling it adds **zero** runtime
dependencies and needs no extra. It is opt-in: nothing happens until you provide
an API key.

**Trade-offs (be honest with users):** this is a *cloud* provider — the text you
synthesize is sent to ElevenLabs, and you are billed per character. Prefer piper
or kokoro for anything private or offline. See ``docs/providers.md`` and
``docs/configuration.md``.

Settings (``providers.elevenlabs`` in the config file):

``api_key``
    ElevenLabs API key. Falls back to the ``ELEVENLABS_API_KEY`` env variable.
``model_id``
    Model id (default ``eleven_multilingual_v2``).
``default_voice``
    Voice id used when a request names none (default ``21m00Tcm4TlvDq8ikWAM``,
    the stock "Rachel" voice).
``base_url``
    API base (default ``https://api.elevenlabs.io/v1``).
``timeout_seconds``
    Per-request timeout (default 30) so a hung call cannot wedge the queue.

Per-request ``options`` understood by this provider:

``stability`` / ``similarity_boost``
    Forwarded verbatim in ElevenLabs' ``voice_settings`` (floats in ``[0, 1]``).

``request.speed`` is **not** mapped: ElevenLabs' TTS has no rate multiplier with
the gateway's ``0.25`` to ``4.0`` semantics, so speed is ignored here (documented).
"""

from __future__ import annotations

import json
import logging
import os

from tts_daemon.core.errors import SynthesisError
from tts_daemon.core.interfaces import TTSProvider
from tts_daemon.core.models import AudioClip, AudioFormat, Availability, SynthesisRequest, Voice
from tts_daemon.providers import _http

logger = logging.getLogger(__name__)

_DEFAULT_MODEL_ID = "eleven_multilingual_v2"
_DEFAULT_VOICE = "21m00Tcm4TlvDq8ikWAM"  # "Rachel", a stock ElevenLabs voice
_DEFAULT_BASE_URL = "https://api.elevenlabs.io/v1"
_DEFAULT_TIMEOUT = 30.0
_PASSTHROUGH_OPTIONS = ("stability", "similarity_boost")


class ElevenLabsProvider(TTSProvider):
    """Synthesize speech via the ElevenLabs cloud API (stdlib ``urllib`` only)."""

    name = "elevenlabs"

    def __init__(self, settings: dict | None = None) -> None:
        super().__init__(settings)
        self._api_key = str(
            self.settings.get("api_key") or os.environ.get("ELEVENLABS_API_KEY") or ""
        )
        self._model_id = str(self.settings.get("model_id") or _DEFAULT_MODEL_ID)
        self._default_voice = str(self.settings.get("default_voice") or _DEFAULT_VOICE)
        self._base_url = str(self.settings.get("base_url") or _DEFAULT_BASE_URL).rstrip("/")
        self._timeout = float(self.settings.get("timeout_seconds") or _DEFAULT_TIMEOUT)
        self._voices_cache: list[Voice] | None = None

    # ------------------------------------------------------------------ api

    def synthesize(self, request: SynthesisRequest) -> AudioClip:
        if request.speed <= 0:
            raise SynthesisError(f"speed must be positive, got {request.speed}")
        if not self._api_key:
            raise SynthesisError(
                "elevenlabs api_key is not set "
                "(providers.elevenlabs.api_key or $ELEVENLABS_API_KEY)"
            )

        options = dict(request.options)
        voice_settings = {
            key: _as_number(key, options.pop(key)) for key in _PASSTHROUGH_OPTIONS if key in options
        }
        if options:
            unknown = ", ".join(sorted(options))
            raise SynthesisError(
                f"Unknown elevenlabs options: {unknown} "
                f"(supported: {', '.join(_PASSTHROUGH_OPTIONS)})"
            )

        body: dict[str, object] = {"text": request.text, "model_id": self._model_id}
        if voice_settings:
            body["voice_settings"] = voice_settings

        voice = request.voice or self._default_voice
        data = _http.request_bytes(
            f"{self._base_url}/text-to-speech/{voice}",
            method="POST",
            headers={
                "xi-api-key": self._api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            data=json.dumps(body).encode("utf-8"),
            timeout=self._timeout,
            provider="elevenlabs",
            extract_error=_extract_error,
        )
        if not data:
            raise SynthesisError(f"elevenlabs returned no audio for voice {voice!r}")
        return AudioClip(data=data, format=AudioFormat.MP3)

    def voices(self) -> list[Voice]:
        """Voices fetched from the API (cached). Empty + logged when no key.

        Never raises: a missing key or a failed fetch yields ``[]`` so one cloud
        provider cannot hide the others in ``/v1/voices``. Only a successful
        fetch is cached, so setting the key later still works.
        """
        if self._voices_cache is not None:
            return self._voices_cache
        if not self._api_key:
            logger.warning(
                "elevenlabs voices() needs an api_key (providers.elevenlabs.api_key); "
                "returning no voices"
            )
            return []
        try:
            raw = _http.request_bytes(
                f"{self._base_url}/voices",
                method="GET",
                headers={"xi-api-key": self._api_key, "Accept": "application/json"},
                timeout=self._timeout,
                provider="elevenlabs",
                extract_error=_extract_error,
            )
            payload = json.loads(raw)
        except (SynthesisError, json.JSONDecodeError, UnicodeDecodeError):
            logger.exception("elevenlabs voice listing failed")
            return []
        entries = payload.get("voices", []) if isinstance(payload, dict) else []
        self._voices_cache = [_to_voice(entry) for entry in entries if isinstance(entry, dict)]
        return self._voices_cache

    def availability(self) -> Availability:
        # Local check only (no network): is there a key to authenticate with?
        if not self._api_key:
            return Availability.unavailable(
                "set providers.elevenlabs.api_key or $ELEVENLABS_API_KEY "
                "(elevenlabs is a paid cloud provider)"
            )
        return Availability.ok()


def _as_number(key: str, value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise SynthesisError(f"elevenlabs option {key!r} must be a number, got {value!r}") from exc


def _to_voice(entry: dict) -> Voice:
    labels = entry.get("labels") if isinstance(entry.get("labels"), dict) else {}
    voice_id = str(entry.get("voice_id") or "")
    description = entry.get("description") or (labels.get("description") if labels else None)
    return Voice(
        id=voice_id,
        name=str(entry.get("name") or voice_id),
        language=labels.get("language") if labels else None,
        description=description,
    )


def _extract_error(body: bytes) -> str:
    """Pull the message out of ElevenLabs' ``{"detail": ...}`` error body."""
    try:
        payload = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return body.decode("utf-8", "replace")
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, dict):
        return str(detail.get("message") or detail)
    if detail:
        return str(detail)
    return ""
