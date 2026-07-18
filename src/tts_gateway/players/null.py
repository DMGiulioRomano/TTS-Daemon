"""A player that discards audio.

Used when ``playback.backend`` is ``null``: the gateway synthesizes (so
``/v1/synthesize`` and provider checks work) but never touches the sound
device. Also handy in tests and headless deployments.
"""

from __future__ import annotations

import threading

from tts_gateway.core.interfaces import AudioPlayer
from tts_gateway.core.models import AudioClip


class NullPlayer(AudioPlayer):
    """Accepts every clip instantly and remembers how many were "played"."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._played = 0

    @property
    def played_count(self) -> int:
        with self._lock:
            return self._played

    def play(self, clip: AudioClip) -> bool:
        with self._lock:
            self._played += 1
        return True

    def stop(self) -> None:  # nothing is ever in progress
        return None
