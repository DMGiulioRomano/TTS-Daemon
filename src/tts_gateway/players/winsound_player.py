"""WAV playback through the standard library ``winsound`` module.

Windows fallback used when no external playback command (ffplay, mpv) is
installed. Only importable on Windows; ``create_player`` guards the import.
"""

from __future__ import annotations

import threading
import winsound

from tts_gateway.core.errors import PlaybackError
from tts_gateway.core.interfaces import AudioPlayer
from tts_gateway.core.models import AudioClip, AudioFormat


class WinsoundPlayer(AudioPlayer):
    """Blocking WAV playback with interrupt support via ``SND_PURGE``."""

    def __init__(self) -> None:
        # winsound plays one sound per process; serialize access.
        self._play_lock = threading.Lock()

    def play(self, clip: AudioClip) -> bool:
        if clip.format is not AudioFormat.WAV:
            raise PlaybackError(
                f"winsound can only play WAV audio, got {clip.format.value}; "
                "install ffplay or mpv, or set playback.command"
            )
        with self._play_lock:
            try:
                winsound.PlaySound(clip.data, winsound.SND_MEMORY)
            except RuntimeError as exc:
                raise PlaybackError(f"winsound failed to play clip: {exc}") from exc
        return True

    def stop(self) -> None:
        # SND_PURGE stops the sound currently playing for this process.
        winsound.PlaySound(None, winsound.SND_PURGE)
