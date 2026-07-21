"""edge-tts provider: free Microsoft neural voices, zero setup.

The `edge-tts <https://pypi.org/project/edge-tts/>`_ package reaches hundreds
of high-quality Microsoft neural voices with **no API key, no GPU, and no model
downloads** — the shortest path from install to "wow, a real voice". It is an
optional extra (``pip install 'tts-daemon[edge]'``); the gateway never imports
it unless this provider is used.

Trade-offs to know (documented in ``docs/providers.md``): it is cloud-backed
(your text is sent to Microsoft), uses an unofficial endpoint that can change,
and needs network access.

Settings (``providers.edge`` in the config file):

``default_voice``
    Voice used when a request names none (default ``en-US-AriaNeural``).

Per-request ``options``:

``volume``
    Volume adjustment string, e.g. ``+50%`` / ``-20%``.
``pitch``
    Pitch adjustment string, e.g. ``+10Hz`` / ``-5Hz``.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging

from tts_daemon.core.errors import SynthesisError
from tts_daemon.core.interfaces import TTSProvider
from tts_daemon.core.models import AudioClip, AudioFormat, Availability, SynthesisRequest, Voice

logger = logging.getLogger(__name__)

_DEFAULT_VOICE = "en-US-AriaNeural"
_INSTALL_HINT = "install with: pip install 'tts-daemon[edge]'"


def _rate(speed: float) -> str:
    """Map a rate multiplier to edge-tts' percentage string (1.5 -> ``+50%``)."""
    return f"{round((speed - 1.0) * 100):+d}%"


class EdgeProvider(TTSProvider):
    """Synthesize speech with Microsoft Edge's online neural voices."""

    name = "edge"

    def __init__(self, settings: dict | None = None) -> None:
        super().__init__(settings)
        self._default_voice = str(self.settings.get("default_voice") or _DEFAULT_VOICE)
        self._voices_cache: list[Voice] | None = None

    def synthesize(self, request: SynthesisRequest) -> AudioClip:
        edge_tts = self._require_package()
        if request.speed <= 0:
            raise SynthesisError(f"speed must be positive, got {request.speed}")

        voice = request.voice or self._default_voice
        options = dict(request.options)
        kwargs: dict[str, str] = {"rate": _rate(request.speed)}
        for key in ("volume", "pitch"):
            value = options.pop(key, None)
            if value is not None:
                kwargs[key] = str(value)
        if options:
            unknown = ", ".join(sorted(options))
            raise SynthesisError(f"Unknown edge options: {unknown} (supported: volume, pitch)")

        try:
            data = asyncio.run(_stream_audio(edge_tts, request.text, voice, kwargs))
        except SynthesisError:
            raise
        except Exception as exc:  # network error, invalid voice, unofficial-API change
            raise SynthesisError(
                f"edge-tts synthesis failed: {exc}. "
                "It needs network access and a valid voice id (see `tts-daemon voices`)."
            ) from exc

        if not data:
            raise SynthesisError(f"edge-tts returned no audio for voice {voice!r}")
        return AudioClip(data=data, format=AudioFormat.MP3)

    def voices(self) -> list[Voice]:
        if importlib.util.find_spec("edge_tts") is None:
            return []
        if self._voices_cache is None:
            try:
                import edge_tts

                raw = asyncio.run(edge_tts.list_voices())
            except Exception:
                logger.exception("Listing edge-tts voices failed")
                return []  # transient (offline): don't cache the empty result
            self._voices_cache = [
                Voice(
                    id=entry.get("ShortName", ""),
                    name=entry.get("FriendlyName") or entry.get("ShortName", ""),
                    language=entry.get("Locale"),
                )
                for entry in raw
                if entry.get("ShortName")
            ]
        return self._voices_cache

    def availability(self) -> Availability:
        if importlib.util.find_spec("edge_tts") is None:
            return Availability.unavailable(f"edge-tts not installed ({_INSTALL_HINT})")
        return Availability.ok()

    def _require_package(self):
        try:
            import edge_tts
        except ImportError as exc:
            raise SynthesisError(f"edge-tts is not installed; {_INSTALL_HINT}") from exc
        return edge_tts


async def _stream_audio(edge_tts, text: str, voice: str, kwargs: dict[str, str]) -> bytes:
    """Collect the MP3 chunks edge-tts streams for one utterance."""
    communicate = edge_tts.Communicate(text, voice, **kwargs)
    audio = bytearray()
    async for chunk in communicate.stream():
        if chunk.get("type") == "audio" and chunk.get("data"):
            audio += chunk["data"]
    return bytes(audio)
