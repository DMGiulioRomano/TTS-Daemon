"""Piper TTS provider.

`Piper <https://github.com/OHF-Voice/piper1-gpl>`_ is a fast, local neural
text-to-speech engine. This provider shells out to the ``piper`` executable,
which keeps the gateway free of heavyweight ML dependencies and works with
both the classic C++ binary and the Python package's CLI.

Settings (``providers.piper`` in the config file):

``binary``
    Executable name or path (default ``piper``).
``models_dir``
    Directory scanned for ``*.onnx`` voice models. Defaults to
    ``$XDG_DATA_HOME/tts-gateway/piper`` (usually
    ``~/.local/share/tts-gateway/piper``).
``default_voice``
    Voice used when a request names none: a model file stem
    (``en_US-lessac-medium``) or a path to an ``.onnx`` file. Defaults to the
    first model in ``models_dir`` (alphabetically).
``speed_flag``
    CLI flag used to pass the length scale. The classic binary spells it
    ``--length_scale`` (default); the Python CLI accepts ``--length-scale``.
``timeout_seconds``
    Maximum wall time for one synthesis (default 120).
``extra_args``
    List of arguments appended verbatim to every invocation.

Per-request ``options`` understood by this provider:

``speaker``
    Speaker id for multi-speaker models (integer).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from json import JSONDecodeError
from json import loads as json_loads
from pathlib import Path

from tts_gateway.core.errors import SynthesisError
from tts_gateway.core.interfaces import TTSProvider
from tts_gateway.core.models import AudioClip, AudioFormat, Availability, SynthesisRequest, Voice

logger = logging.getLogger(__name__)

_STDERR_TAIL_CHARS = 500  # how much of piper's stderr to include in errors


def default_models_dir() -> Path:
    """``$XDG_DATA_HOME/tts-gateway/piper`` with the ``~/.local/share`` fallback."""
    xdg = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "tts-gateway" / "piper"


class PiperProvider(TTSProvider):
    """Synthesize speech by invoking the ``piper`` executable."""

    name = "piper"

    def __init__(self, settings: dict | None = None) -> None:
        super().__init__(settings)
        self._binary: str = str(self.settings.get("binary") or "piper")
        models_dir = self.settings.get("models_dir")
        self._models_dir: Path = (
            Path(models_dir).expanduser() if models_dir else default_models_dir()
        )
        self._default_voice = self.settings.get("default_voice")
        self._speed_flag: str = str(self.settings.get("speed_flag") or "--length_scale")
        self._timeout: float = float(self.settings.get("timeout_seconds") or 120)
        extra_args = self.settings.get("extra_args") or []
        self._extra_args: list[str] = [str(arg) for arg in extra_args]

    # ------------------------------------------------------------------ api

    def synthesize(self, request: SynthesisRequest) -> AudioClip:
        if request.speed <= 0:
            raise SynthesisError(f"speed must be positive, got {request.speed}")
        model_path = self._resolve_model(request.voice)
        command = [self._binary, "--model", str(model_path)]

        if request.speed != 1.0:
            # Piper's length scale stretches phoneme durations, so it is the
            # inverse of a rate multiplier: speed 2.0 -> length scale 0.5.
            command += [self._speed_flag, f"{1.0 / request.speed:.4f}"]

        options = dict(request.options)
        speaker = options.pop("speaker", None)
        if speaker is not None:
            command += ["--speaker", str(int(speaker))]
        if options:
            unknown = ", ".join(sorted(options))
            raise SynthesisError(f"Unknown piper options: {unknown} (supported: speaker)")

        command += self._extra_args

        with tempfile.TemporaryDirectory(prefix="tts-gateway-piper-") as tmp:
            output_path = Path(tmp) / "out.wav"
            command += ["--output_file", str(output_path)]
            logger.debug("Running piper: %s", " ".join(command))
            try:
                completed = subprocess.run(
                    command,
                    input=request.text.encode("utf-8"),
                    capture_output=True,
                    timeout=self._timeout,
                )
            except FileNotFoundError as exc:
                raise SynthesisError(
                    f"piper binary not found: {self._binary!r}. "
                    "Install piper and/or set providers.piper.binary in the config."
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise SynthesisError(
                    f"piper timed out after {self._timeout:.0f}s for a "
                    f"{len(request.text)}-character text"
                ) from exc

            if completed.returncode != 0:
                raise SynthesisError(
                    f"piper exited with status {completed.returncode}: "
                    f"{_stderr_tail(completed.stderr)}"
                )
            try:
                data = output_path.read_bytes()
            except OSError as exc:
                raise SynthesisError(
                    f"piper reported success but wrote no output file: {exc}"
                ) from exc

        if not data:
            raise SynthesisError("piper produced an empty audio file")
        return AudioClip(data=data, format=AudioFormat.WAV)

    def voices(self) -> list[Voice]:
        voices = []
        for model_path in self._model_files():
            metadata = _read_model_metadata(model_path)
            voices.append(
                Voice(
                    id=model_path.stem,
                    name=metadata.get("name") or model_path.stem,
                    language=metadata.get("language"),
                    description=metadata.get("description"),
                )
            )
        return voices

    def availability(self) -> Availability:
        if shutil.which(self._binary) is None:
            return Availability.unavailable(
                f"piper binary {self._binary!r} not found on PATH "
                "(install piper, or set providers.piper.binary)"
            )
        if self._explicit_default_voice_path() is None and not self._model_files():
            return Availability.unavailable(
                f"no voice models (*.onnx) found in {self._models_dir} "
                "(download one, or set providers.piper.models_dir / default_voice)"
            )
        return Availability.ok()

    # ------------------------------------------------------------- internals

    def _model_files(self) -> list[Path]:
        if not self._models_dir.is_dir():
            return []
        return sorted(path for path in self._models_dir.glob("*.onnx") if path.is_file())

    def _explicit_default_voice_path(self) -> Path | None:
        """``default_voice`` interpreted as a direct path, if it is one."""
        if not self._default_voice:
            return None
        candidate = Path(str(self._default_voice)).expanduser()
        if candidate.suffix == ".onnx" and candidate.is_file():
            return candidate
        return None

    def _resolve_model(self, voice: str | None) -> Path:
        """Map a requested voice (or the configured/first default) to a model file."""
        requested = voice or self._default_voice
        if requested:
            candidate = Path(str(requested)).expanduser()
            if candidate.suffix == ".onnx":
                if candidate.is_file():
                    return candidate
                raise SynthesisError(f"Voice model file not found: {candidate}")
            named = self._models_dir / f"{requested}.onnx"
            if named.is_file():
                return named
            available = ", ".join(path.stem for path in self._model_files()) or "none"
            raise SynthesisError(
                f"Voice {requested!r} not found in {self._models_dir} (available: {available})"
            )
        models = self._model_files()
        if not models:
            raise SynthesisError(
                f"No piper voice models (*.onnx) found in {self._models_dir}. "
                "Download a voice or set providers.piper.default_voice."
            )
        return models[0]


def _stderr_tail(stderr: bytes | None) -> str:
    """The last part of a stderr capture, cleaned up for an error message."""
    text = (stderr or b"").decode("utf-8", "replace").strip()
    if not text:
        return "(no stderr output)"
    return text[-_STDERR_TAIL_CHARS:]


def _read_model_metadata(model_path: Path) -> dict[str, str | None]:
    """Extract display metadata from the ``.onnx.json`` file piper ships with models."""
    config_path = model_path.parent / (model_path.name + ".json")
    if not config_path.is_file():
        return {}
    try:
        raw = json_loads(config_path.read_text(encoding="utf-8"))
    except (OSError, JSONDecodeError, UnicodeDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    language = None
    language_block = raw.get("language")
    if isinstance(language_block, dict):
        language = language_block.get("code") or language_block.get("family")
    elif isinstance(language_block, str):
        language = language_block
    dataset = raw.get("dataset")
    audio_block = raw.get("audio")
    quality = audio_block.get("quality") if isinstance(audio_block, dict) else None
    description = None
    if dataset or quality:
        description = " ".join(str(part) for part in (dataset, quality) if part)
    return {"name": dataset or None, "language": language, "description": description}
