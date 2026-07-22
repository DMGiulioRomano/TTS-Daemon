"""Kokoro TTS provider: a small, high-quality *local* neural engine.

`Kokoro <https://github.com/thewh1teagle/kokoro-onnx>`_ (~82M parameters) runs
through ONNX Runtime on CPU — near-Piper speed with noticeably better prosody,
and unlike ``edge`` it is fully offline: nothing leaves the machine. It is the
project's expected second local engine after Piper.

The engine ships as an optional extra so the gateway never depends on it::

    pip install 'tts-daemon[kokoro]'

``kokoro_onnx`` is imported lazily (never at module import time), so the class
loads — and reports itself unavailable, with an actionable hint — even when the
package is absent. Kokoro also needs two downloadable artifacts, a model file
and a voices file; ``availability()`` tells them apart so the message points at
the missing piece.

Settings (``providers.kokoro`` in the config file):

``model_path``
    Path to the ONNX model (``kokoro-v1.0.onnx``). Defaults to
    ``$XDG_DATA_HOME/tts-daemon/kokoro/kokoro-v1.0.onnx``.
``voices_path``
    Path to the voices file (``voices-v1.0.bin``). Defaults to
    ``$XDG_DATA_HOME/tts-daemon/kokoro/voices-v1.0.bin``.
``default_voice``
    Voice used when a request names none (default ``af_sarah``). List the
    bundled names with ``tts-daemon voices --provider kokoro``.
``lang``
    espeak language code for the engine's grapheme-to-phoneme step (default
    ``en-us``); also overridable per request via ``options.lang``.

Per-request ``options`` understood by this provider:

``lang``
    Overrides the configured language for one request. Values are espeak
    language codes — e.g. ``"en-us"``, ``"fr-fr"``, ``"cmn"`` — so the code and
    the text's script must agree (``"fr"`` / ``"zh"`` are rejected by espeak).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from tts_daemon.core.errors import SynthesisError
from tts_daemon.core.interfaces import TTSProvider
from tts_daemon.core.models import AudioClip, AudioFormat, Availability, SynthesisRequest, Voice
from tts_daemon.providers._audio import floats_to_wav

logger = logging.getLogger(__name__)

_DEFAULT_VOICE = "af_sarah"
_DEFAULT_LANG = "en-us"
_MODEL_FILE = "kokoro-v1.0.onnx"
_VOICES_FILE = "voices-v1.0.bin"
_RELEASE_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
_INSTALL_HINT = "install it with: pip install 'tts-daemon[kokoro]'"


def default_kokoro_dir() -> Path:
    """``$XDG_DATA_HOME/tts-daemon/kokoro`` with the ``~/.local/share`` fallback."""
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "tts-daemon" / "kokoro"


class KokoroProvider(TTSProvider):
    """Synthesize speech via the (optional) ``kokoro-onnx`` package."""

    name = "kokoro"

    def __init__(self, settings: dict | None = None) -> None:
        super().__init__(settings)
        model_path = self.settings.get("model_path")
        voices_path = self.settings.get("voices_path")
        self._model_path: Path = (
            Path(model_path).expanduser() if model_path else default_kokoro_dir() / _MODEL_FILE
        )
        self._voices_path: Path = (
            Path(voices_path).expanduser() if voices_path else default_kokoro_dir() / _VOICES_FILE
        )
        self._default_voice = str(self.settings.get("default_voice") or _DEFAULT_VOICE)
        self._lang = str(self.settings.get("lang") or _DEFAULT_LANG)
        # The ONNX session is expensive to build, so cache the loaded engine.
        self._engine: Any | None = None

    # ------------------------------------------------------------------ api

    def synthesize(self, request: SynthesisRequest) -> AudioClip:
        if request.speed <= 0:
            raise SynthesisError(f"speed must be positive, got {request.speed}")
        options = dict(request.options)
        lang = options.pop("lang", self._lang)
        if options:
            unknown = ", ".join(sorted(options))
            raise SynthesisError(f"Unknown kokoro options: {unknown} (supported: lang)")

        engine = self._load()
        voice = request.voice or self._default_voice
        try:
            samples, sample_rate = engine.create(
                request.text, voice=voice, speed=request.speed, lang=str(lang)
            )
        except SynthesisError:
            raise
        except Exception as exc:
            raise SynthesisError(
                f"kokoro synthesis failed: {exc}. Check that the voice {voice!r} exists "
                "(list them with 'tts-daemon voices --provider kokoro') and that the model "
                "and voices files are the matching kokoro release."
            ) from exc

        if samples is None or len(samples) == 0:
            raise SynthesisError(f"kokoro produced no audio for voice {voice!r}")
        data = floats_to_wav(_as_floats(samples), int(sample_rate))
        return AudioClip(data=data, format=AudioFormat.WAV)

    def voices(self) -> list[Voice]:
        """Voice names from the loaded voices file (empty if unavailable).

        Never raises: a missing package or model yields ``[]`` so one provider
        cannot hide the others in ``/v1/voices``.
        """
        try:
            engine = self._load()
        except SynthesisError:
            return []
        try:
            names = engine.get_voices()
        except Exception:
            logger.exception("kokoro voice listing failed")
            return []
        return [Voice(id=str(name), name=str(name)) for name in names]

    def synthesis_fingerprint(self, request: SynthesisRequest) -> str:
        """Fold the model file's mtime into the cache key (best-effort).

        Swapping the model in place (same path, new bytes) must invalidate any
        clips synthesized from the old one; fall back to the provider name when
        the file cannot be stat'd yet.
        """
        try:
            return f"kokoro:{self._model_path}:{self._model_path.stat().st_mtime_ns}"
        except OSError:
            return self.name

    def availability(self) -> Availability:
        # Fast, local checks only (called on every /v1/providers hit and during
        # auto-resolution): the package import, then each downloadable artifact.
        # The three reasons are kept distinct so the hint names the missing piece.
        if self._import_kokoro() is None:
            return Availability.unavailable(f"kokoro-onnx is not installed; {_INSTALL_HINT}")
        if not self._model_path.is_file():
            return Availability.unavailable(
                f"kokoro model not found at {self._model_path} "
                f"(download {_MODEL_FILE} from {_RELEASE_URL}, "
                "or set providers.kokoro.model_path)"
            )
        if not self._voices_path.is_file():
            return Availability.unavailable(
                f"kokoro voices file not found at {self._voices_path} "
                f"(download {_VOICES_FILE} from {_RELEASE_URL}, "
                "or set providers.kokoro.voices_path)"
            )
        return Availability.ok()

    def close(self) -> None:
        self._engine = None

    # ------------------------------------------------------------- internals

    def _load(self) -> Any:
        """Return the cached Kokoro engine, building it on first use.

        Raises :class:`SynthesisError` (never a bare exception) when the
        package or either artifact is missing, or the model fails to load.
        """
        if self._engine is not None:
            return self._engine
        module = self._import_kokoro()
        if module is None:
            raise SynthesisError(f"kokoro-onnx is not installed — {_INSTALL_HINT}")
        if not self._model_path.is_file():
            raise SynthesisError(
                f"kokoro model not found at {self._model_path} "
                f"(download {_MODEL_FILE} from {_RELEASE_URL}, or set providers.kokoro.model_path)"
            )
        if not self._voices_path.is_file():
            raise SynthesisError(
                f"kokoro voices file not found at {self._voices_path} "
                f"(download {_VOICES_FILE} from {_RELEASE_URL}, "
                "or set providers.kokoro.voices_path)"
            )
        try:
            self._engine = module.Kokoro(str(self._model_path), str(self._voices_path))
        except Exception as exc:
            raise SynthesisError(f"failed to load the kokoro model: {exc}") from exc
        return self._engine

    @staticmethod
    def _import_kokoro() -> Any:
        try:
            import kokoro_onnx
        except ImportError:
            return None
        return kokoro_onnx


def _as_floats(samples: Any) -> list[float]:
    """Normalise engine output (a numpy array or a plain sequence) to a list."""
    tolist = getattr(samples, "tolist", None)
    if callable(tolist):
        return tolist()
    return list(samples)
