"""Play audio through an external command (``paplay``, ``ffplay``, ``afplay``...).

Shelling out keeps the gateway dependency-free and works with whatever audio
stack the machine already has. The player either runs a user-configured argv
or auto-detects the best installed candidate for the clip's format.

The argv may contain the placeholder ``{file}``, replaced with the path of a
temporary file holding the clip; an argv without the placeholder receives the
audio bytes on stdin.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from tts_gateway.core.errors import PlaybackError
from tts_gateway.core.interfaces import AudioPlayer
from tts_gateway.core.models import AudioClip, AudioFormat, Availability

logger = logging.getLogger(__name__)

FILE_PLACEHOLDER = "{file}"
_STOP_GRACE_SECONDS = 1.0  # after terminate(), how long before kill()


@dataclass(frozen=True)
class Candidate:
    """A known playback command and the formats it can decode."""

    executable: str
    argv: tuple[str, ...]
    #: ``None`` means "plays anything we synthesize".
    formats: frozenset[AudioFormat] | None


_LIBSNDFILE_FORMATS = frozenset({AudioFormat.WAV, AudioFormat.FLAC, AudioFormat.OGG})

# Ordered by preference: native sound-server clients first (lowest latency,
# they respect per-application volume), generic media players last.
_POSIX_CANDIDATES: tuple[Candidate, ...] = (
    Candidate("pw-play", ("pw-play", FILE_PLACEHOLDER), _LIBSNDFILE_FORMATS),
    Candidate("paplay", ("paplay", FILE_PLACEHOLDER), _LIBSNDFILE_FORMATS),
    Candidate("aplay", ("aplay", "-q", FILE_PLACEHOLDER), frozenset({AudioFormat.WAV})),
    Candidate(
        "ffplay",
        ("ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet", FILE_PLACEHOLDER),
        None,
    ),
    Candidate("mpv", ("mpv", "--really-quiet", "--no-video", FILE_PLACEHOLDER), None),
    Candidate("play", ("play", "-q", FILE_PLACEHOLDER), None),  # SoX
)

_AFPLAY_FORMATS = frozenset({AudioFormat.WAV, AudioFormat.MP3})

_DARWIN_CANDIDATES: tuple[Candidate, ...] = (
    Candidate("afplay", ("afplay", FILE_PLACEHOLDER), _AFPLAY_FORMATS),
    *_POSIX_CANDIDATES,
)

_WINDOWS_CANDIDATES: tuple[Candidate, ...] = (
    Candidate(
        "ffplay",
        ("ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet", FILE_PLACEHOLDER),
        None,
    ),
    Candidate("mpv", ("mpv", "--really-quiet", "--no-video", FILE_PLACEHOLDER), None),
)


def _platform_candidates() -> tuple[Candidate, ...]:
    if sys.platform == "darwin":
        return _DARWIN_CANDIDATES
    if sys.platform == "win32":
        return _WINDOWS_CANDIDATES
    return _POSIX_CANDIDATES


class _PlaySession:
    """One clip being played; owns the subprocess so stop() can kill it safely."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._process: subprocess.Popen[bytes] | None = None
        self.stopped = False

    def launch(
        self, argv: list[str], *, stdin_data: bytes | None
    ) -> subprocess.Popen[bytes] | None:
        """Start the process unless stop() already happened. Returns the process."""
        with self._lock:
            if self.stopped:
                return None
            self._process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                # Own process group (POSIX) so stop() can signal shell
                # wrappers *and* their children; an orphaned child would
                # otherwise keep pipes open and block communicate().
                start_new_session=(sys.platform != "win32"),
            )
            return self._process

    def stop(self) -> None:
        with self._lock:
            self.stopped = True
            process = self._process
        if process is None or process.poll() is not None:
            return
        _signal_tree(process, signal.SIGTERM)
        try:
            process.wait(timeout=_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _signal_tree(process, signal.SIGKILL)


def _signal_tree(process: subprocess.Popen[bytes], signum: signal.Signals) -> None:
    """Signal the player's whole process group (falling back to the process)."""
    if sys.platform != "win32":
        try:
            os.killpg(os.getpgid(process.pid), signum)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass  # already gone, or not group leader: fall through
    try:
        if signum == getattr(signal, "SIGKILL", None):
            process.kill()
        else:
            process.terminate()
    except OSError:  # pragma: no cover - process vanished between checks
        pass


class CommandPlayer(AudioPlayer):
    """Blocking, interruptible playback via an external command."""

    def __init__(self, command: list[str] | None = None) -> None:
        self._configured_argv: tuple[str, ...] | None = tuple(command) if command else None
        self._lock = threading.Lock()
        self._session: _PlaySession | None = None

    # ----------------------------------------------------------- detection

    @staticmethod
    def detect_candidates() -> list[Candidate]:
        """Installed playback commands for this platform, best first."""
        return [
            candidate
            for candidate in _platform_candidates()
            if shutil.which(candidate.executable) is not None
        ]

    def _argv_for(self, format_: AudioFormat) -> tuple[str, ...]:
        if self._configured_argv is not None:
            return self._configured_argv
        for candidate in self.detect_candidates():
            if candidate.formats is None or format_ in candidate.formats:
                return candidate.argv
        raise PlaybackError(
            f"No playback command found for {format_.value} audio. Install one of: "
            + ", ".join(c.executable for c in _platform_candidates())
            + ", or set playback.command in the configuration."
        )

    def availability(self) -> Availability:
        if self._configured_argv is not None:
            executable = self._configured_argv[0]
            if shutil.which(executable) is None:
                return Availability.unavailable(
                    f"configured playback command {executable!r} not found on PATH"
                )
            return Availability.ok()
        if not self.detect_candidates():
            return Availability.unavailable(
                "no playback command found; install one of "
                + ", ".join(c.executable for c in _platform_candidates())
                + " or set playback.command"
            )
        return Availability.ok()

    # ------------------------------------------------------------- playing

    def play(self, clip: AudioClip) -> bool:
        argv_template = self._argv_for(clip.format)
        session = _PlaySession()
        with self._lock:
            self._session = session
        try:
            return self._run(session, argv_template, clip)
        finally:
            with self._lock:
                if self._session is session:
                    self._session = None

    def _run(self, session: _PlaySession, argv_template: tuple[str, ...], clip: AudioClip) -> bool:
        uses_file = any(FILE_PLACEHOLDER in arg for arg in argv_template)
        temp_path: Path | None = None
        try:
            if uses_file:
                # delete=False so the path can be handed to another process
                # (required on Windows, harmless elsewhere); removed in finally.
                with tempfile.NamedTemporaryFile(
                    prefix="tts-gateway-", suffix=clip.format.suffix, delete=False
                ) as handle:
                    handle.write(clip.data)
                    temp_path = Path(handle.name)
                argv = [arg.replace(FILE_PLACEHOLDER, str(temp_path)) for arg in argv_template]
                stdin_data = None
            else:
                argv = list(argv_template)
                stdin_data = clip.data

            try:
                process = session.launch(argv, stdin_data=stdin_data)
            except FileNotFoundError as exc:
                raise PlaybackError(f"Playback command not found: {argv[0]!r}") from exc
            except OSError as exc:
                raise PlaybackError(f"Failed to start playback command {argv[0]!r}: {exc}") from exc
            if process is None:  # stopped before launch
                return False

            _, stderr = process.communicate(input=stdin_data)
            if session.stopped:
                return False
            if process.returncode != 0:
                detail = (stderr or b"").decode("utf-8", "replace").strip()[-400:]
                raise PlaybackError(
                    f"Playback command {argv[0]!r} exited with status {process.returncode}"
                    + (f": {detail}" if detail else "")
                )
            return True
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def stop(self) -> None:
        with self._lock:
            session = self._session
        if session is not None:
            session.stop()

    def close(self) -> None:
        self.stop()
