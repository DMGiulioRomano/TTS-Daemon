"""edge-tts provider: free Microsoft neural voices, zero local setup.

`edge-tts <https://pypi.org/project/edge-tts/>`_ speaks through Microsoft Edge's
online text-to-speech service — hundreds of neural voices in dozens of
languages, with no API key, GPU, or model download. It is the shortest path from
a fresh install to a natural-sounding voice.

**Trade-offs (state them to users):** it is *cloud-backed* — the text you
synthesize is sent to Microsoft; it uses an *unofficial* endpoint that can change
or break; and it needs network access. See ``docs/providers.md`` and
``docs/configuration.md`` for the privacy note.

The package is an optional extra so the gateway never depends on it::

    pip install 'tts-daemon[edge]'

``edge-tts`` is imported lazily (never at module import time), so the provider
class loads — and reports itself unavailable — even when the package is absent.
The package is asyncio-based; because the gateway calls ``synthesize`` on a
queue worker thread (never the event loop), a private ``asyncio.run(...)`` is
safe here.

Settings (``providers.edge`` in the config file):

``default_voice``
    Voice used when a request names none (default ``en-US-AriaNeural``).
``default_pitch`` / ``default_volume``
    Optional defaults for the edge ``pitch`` / ``volume`` parameters, applied
    when a request does not override them via ``options``.

Per-request ``options`` understood by this provider:

``pitch``
    Passed to edge-tts verbatim, e.g. ``"+10Hz"`` or ``"-5Hz"``.
``volume``
    Passed to edge-tts verbatim, e.g. ``"+0%"`` or ``"-20%"``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from tts_daemon.core.errors import SynthesisError
from tts_daemon.core.interfaces import TTSProvider
from tts_daemon.core.models import AudioClip, AudioFormat, Availability, SynthesisRequest, Voice

logger = logging.getLogger(__name__)

_DEFAULT_VOICE = "en-US-AriaNeural"
_INSTALL_HINT = "install it with: pip install 'tts-daemon[edge]'"


def _rate_from_speed(speed: float) -> str:
    """Map the gateway's rate multiplier onto edge-tts' ``rate`` string.

    ``1.0`` -> ``"+0%"``, ``1.5`` -> ``"+50%"``, ``0.5`` -> ``"-50%"``.
    """
    percent = round((speed - 1.0) * 100)
    return f"{percent:+d}%"


def _to_voice(entry: dict[str, Any]) -> Voice:
    short = entry.get("ShortName") or entry.get("Name") or ""
    return Voice(
        id=short,
        name=entry.get("FriendlyName") or short,
        language=entry.get("Locale"),
        description=entry.get("Gender") or None,
    )


async def _collect_audio(communicate: Any) -> bytes:
    """Drain edge-tts' async stream, keeping only the audio chunks."""
    chunks = bytearray()
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            chunks += chunk["data"]
    return bytes(chunks)


class EdgeProvider(TTSProvider):
    """Synthesize speech via the (optional) ``edge-tts`` package."""

    name = "edge"

    def __init__(self, settings: dict | None = None) -> None:
        super().__init__(settings)
        self._default_voice = str(self.settings.get("default_voice") or _DEFAULT_VOICE)
        self._default_pitch = self.settings.get("default_pitch")
        self._default_volume = self.settings.get("default_volume")
        self._voices_cache: list[Voice] | None = None

    # ------------------------------------------------------------------ api

    def synthesize(self, request: SynthesisRequest) -> AudioClip:
        if request.speed <= 0:
            raise SynthesisError(f"speed must be positive, got {request.speed}")
        edge_tts = self._require_edge()

        voice = request.voice or self._default_voice
        kwargs: dict[str, str] = {"rate": _rate_from_speed(request.speed)}
        options = dict(request.options)
        pitch = options.pop("pitch", self._default_pitch)
        volume = options.pop("volume", self._default_volume)
        if options:
            unknown = ", ".join(sorted(options))
            raise SynthesisError(f"Unknown edge options: {unknown} (supported: pitch, volume)")
        if pitch is not None:
            kwargs["pitch"] = str(pitch)
        if volume is not None:
            kwargs["volume"] = str(volume)

        try:
            communicate = edge_tts.Communicate(request.text, voice, **kwargs)
            data = asyncio.run(_collect_audio(communicate))
        except SynthesisError:
            raise
        except Exception as exc:
            raise SynthesisError(
                f"edge-tts synthesis failed: {exc}. edge-tts is a cloud provider "
                "(an unofficial Microsoft endpoint): it needs network access, and the "
                f"voice {voice!r} must exist (list them with 'tts-daemon voices --provider edge')."
            ) from exc

        if not data:
            raise SynthesisError(
                f"edge-tts returned no audio for voice {voice!r} "
                "(unknown voice, or the endpoint refused the request)"
            )
        return AudioClip(data=data, format=AudioFormat.MP3)

    def voices(self) -> list[Voice]:
        """Voices from the edge-tts catalog (cached; empty if unavailable).

        Never raises: a missing package or a failed catalog fetch yields ``[]``
        so one cloud provider cannot hide the others in ``/v1/voices``.
        """
        if self._voices_cache is not None:
            return self._voices_cache
        edge_tts = self._import_edge()
        if edge_tts is None:
            return []
        try:
            raw = asyncio.run(edge_tts.list_voices())
        except Exception:
            logger.exception("edge-tts voice listing failed")
            return []
        self._voices_cache = [_to_voice(entry) for entry in raw if isinstance(entry, dict)]
        return self._voices_cache

    def availability(self) -> Availability:
        # Only the (fast, local) package check runs here — availability() is
        # called on every /v1/providers hit and during auto-resolution, so it
        # must not probe the network. Offline/endpoint failures surface with an
        # actionable message from synthesize() instead.
        if self._import_edge() is None:
            return Availability.unavailable(f"edge-tts is not installed; {_INSTALL_HINT}")
        return Availability.ok()

    # ------------------------------------------------------------- internals

    def _require_edge(self) -> Any:
        module = self._import_edge()
        if module is None:
            raise SynthesisError(f"edge-tts is not installed — {_INSTALL_HINT}")
        return module

    @staticmethod
    def _import_edge() -> Any:
        try:
            import edge_tts
        except ImportError:
            return None
        return edge_tts
