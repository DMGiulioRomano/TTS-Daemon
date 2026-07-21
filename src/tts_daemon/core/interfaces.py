"""Ports of the gateway: the interfaces that adapters implement.

To add a new TTS engine you implement :class:`TTSProvider` and register it
(see ``tts_daemon.providers.registry``); to add a new audio output you
implement :class:`AudioPlayer`. Nothing else in the gateway needs to change.

Both interfaces are deliberately synchronous. Providers are typically
subprocess or HTTP calls and players block for the duration of a clip; the
gateway runs them on worker threads so the API event loop is never blocked.
A synchronous contract keeps third-party implementations trivial to write
and test.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from tts_daemon.core.models import AudioClip, Availability, SynthesisRequest, Voice


class TTSProvider(ABC):
    """A text-to-speech engine.

    Implementations must be safe to call from a single worker thread at a
    time (the gateway never calls ``synthesize`` concurrently on the same
    instance). Constructors receive a plain ``dict`` of provider settings
    taken from the ``providers.<name>`` section of the configuration, so a
    provider is fully described by its class plus its settings mapping.
    """

    #: Registry key and API identifier for this provider ("piper", "tone"...).
    name: ClassVar[str] = ""

    def __init__(self, settings: dict | None = None) -> None:
        self.settings = dict(settings or {})

    @abstractmethod
    def synthesize(self, request: SynthesisRequest) -> AudioClip:
        """Convert ``request.text`` into audio.

        Raise :class:`~tts_daemon.core.errors.SynthesisError` on failure;
        any other exception is treated as a bug in the provider.
        """

    @abstractmethod
    def voices(self) -> list[Voice]:
        """Voices currently usable with this provider (may be empty)."""

    def availability(self) -> Availability:
        """Report whether the provider can synthesize right now.

        Called before selecting a provider (e.g. for ``auto`` resolution) and
        surfaced verbatim through the ``/v1/providers`` endpoint, so the
        ``reason`` should tell a human how to fix the problem ("piper binary
        not found on PATH", "no .onnx model in ~/.local/share/...").
        """
        return Availability.ok()

    def cache_fingerprint(self, request: SynthesisRequest) -> str:
        """Extra material folded into the synthesis cache key for this request.

        The gateway already keys the cache on provider name, voice, speed,
        options, and text. Override this to add engine state that those don't
        capture — e.g. the mtime of the resolved voice model — so that swapping
        a model file invalidates cached audio. The default adds nothing.
        """
        return ""

    def close(self) -> None:  # noqa: B027 - optional hook, a no-op default is the contract
        """Release resources (subprocesses, HTTP sessions). Idempotent."""


class AudioPlayer(ABC):
    """An audio output backend.

    ``play`` blocks until the clip has finished or :meth:`stop` was called
    from another thread. This blocking contract is what gives the gateway
    ordered, interruptible playback with no further coordination.
    """

    @abstractmethod
    def play(self, clip: AudioClip) -> bool:
        """Play ``clip`` to completion.

        Returns ``True`` if the clip played fully, ``False`` if it was
        interrupted by :meth:`stop`. Raises
        :class:`~tts_daemon.core.errors.PlaybackError` when playback cannot
        run at all (no output command, unsupported format...).
        """

    @abstractmethod
    def stop(self) -> None:
        """Interrupt the clip currently playing, if any. Thread-safe."""

    def availability(self) -> Availability:
        """Whether the player can produce sound on this machine."""
        return Availability.ok()

    def close(self) -> None:  # noqa: B027 - optional hook, a no-op default is the contract
        """Release resources. Idempotent."""
