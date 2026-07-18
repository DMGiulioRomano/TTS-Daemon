"""Domain models shared by every layer of the gateway.

These are plain dataclasses and enums with no framework dependencies, so that
providers, players, the queue, and the API layer all speak the same language.
"""

from __future__ import annotations

import io
import threading
import time
import uuid
import wave
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AudioFormat(str, Enum):
    """Container format of a synthesized audio clip."""

    WAV = "wav"
    MP3 = "mp3"
    OGG = "ogg"
    FLAC = "flac"

    @property
    def media_type(self) -> str:
        """The MIME type used when serving this format over HTTP."""
        return _MEDIA_TYPES[self]

    @property
    def suffix(self) -> str:
        """File suffix (including the dot) used for temporary files."""
        return f".{self.value}"


_MEDIA_TYPES: dict[AudioFormat, str] = {
    AudioFormat.WAV: "audio/wav",
    AudioFormat.MP3: "audio/mpeg",
    AudioFormat.OGG: "audio/ogg",
    AudioFormat.FLAC: "audio/flac",
}


@dataclass(frozen=True)
class AudioClip:
    """A fully synthesized piece of audio, ready to be played or served.

    Providers return complete clips rather than streams; this keeps the
    provider contract simple and makes interruption trivial (stop the player,
    drop the clip).
    """

    data: bytes
    format: AudioFormat = AudioFormat.WAV

    @property
    def media_type(self) -> str:
        return self.format.media_type

    @property
    def duration_seconds(self) -> float | None:
        """Clip duration, when it can be determined cheaply.

        Only WAV clips are inspected (a header read); for compressed formats
        ``None`` is returned rather than pulling in a decoding dependency.
        """
        if self.format is not AudioFormat.WAV:
            return None
        try:
            with wave.open(io.BytesIO(self.data)) as wav:
                rate = wav.getframerate()
                if rate <= 0:
                    return None
                return wav.getnframes() / rate
        except (wave.Error, EOFError):
            return None


@dataclass(frozen=True)
class Voice:
    """A voice offered by a provider.

    ``id`` is what clients pass back in ``SynthesisRequest.voice``; ``name``
    is a human-friendly label.
    """

    id: str
    name: str
    language: str | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "language": self.language,
            "description": self.description,
        }


@dataclass(frozen=True)
class Availability:
    """Whether a provider (or player) can currently be used, and if not, why."""

    available: bool
    reason: str = ""

    @classmethod
    def ok(cls) -> Availability:
        return cls(available=True)

    @classmethod
    def unavailable(cls, reason: str) -> Availability:
        return cls(available=False, reason=reason)


@dataclass(frozen=True)
class SynthesisRequest:
    """Everything a provider needs to turn text into audio.

    ``speed`` is a rate multiplier: ``1.0`` is the voice's natural rate,
    ``2.0`` is twice as fast, ``0.5`` half speed. Providers translate this to
    their native parameter (e.g. Piper's ``--length-scale`` is ``1/speed``).

    ``options`` carries provider-specific settings that the gateway passes
    through untouched, so new provider features never require API changes.
    """

    text: str
    voice: str | None = None
    speed: float = 1.0
    options: dict[str, Any] = field(default_factory=dict)


class UtteranceState(str, Enum):
    """Lifecycle of a queued utterance.

    ``QUEUED -> SYNTHESIZING -> SPEAKING -> FINISHED`` is the happy path;
    ``CANCELLED`` and ``FAILED`` are terminal alternatives reachable from any
    non-terminal state.
    """

    QUEUED = "queued"
    SYNTHESIZING = "synthesizing"
    SPEAKING = "speaking"
    FINISHED = "finished"
    CANCELLED = "cancelled"
    FAILED = "failed"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATES


_TERMINAL_STATES = frozenset(
    {UtteranceState.FINISHED, UtteranceState.CANCELLED, UtteranceState.FAILED}
)


class Utterance:
    """A single speech job as it moves through the playback queue.

    Instances are shared between the API thread (which creates them and may
    wait on completion) and the queue worker thread (which drives the state
    machine), so state changes go through :meth:`transition` under a lock.
    """

    def __init__(self, request: SynthesisRequest, provider_name: str) -> None:
        self.id: str = uuid.uuid4().hex[:12]
        self.request = request
        self.provider_name = provider_name
        self.created_at: float = time.time()
        self.started_at: float | None = None
        self.finished_at: float | None = None
        self.error: str | None = None
        self._state = UtteranceState.QUEUED
        self._cancel_requested = False
        self._lock = threading.Lock()
        self._done = threading.Event()

    @property
    def state(self) -> UtteranceState:
        with self._lock:
            return self._state

    @property
    def cancel_requested(self) -> bool:
        with self._lock:
            return self._cancel_requested

    def request_cancel(self) -> None:
        """Mark the utterance for cancellation.

        The queue worker honours the flag at its next checkpoint; an
        utterance that is already terminal is unaffected.
        """
        with self._lock:
            self._cancel_requested = True

    def transition(self, state: UtteranceState, *, error: str | None = None) -> bool:
        """Move to ``state``, returning ``False`` if already terminal.

        Terminal states latch: once finished/cancelled/failed, further
        transitions are ignored so racing threads cannot resurrect a job.
        """
        with self._lock:
            if self._state.is_terminal:
                return False
            self._state = state
            if state is UtteranceState.SPEAKING and self.started_at is None:
                self.started_at = time.time()
            if state.is_terminal:
                self.finished_at = time.time()
                self.error = error
                self._done.set()
            return True

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the utterance reaches a terminal state."""
        return self._done.wait(timeout)

    def snapshot(self) -> dict[str, Any]:
        """A JSON-safe view of the utterance for APIs and events."""
        with self._lock:
            return {
                "id": self.id,
                "text": self.request.text,
                "provider": self.provider_name,
                "voice": self.request.voice,
                "speed": self.request.speed,
                "state": self._state.value,
                "error": self.error,
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
            }
