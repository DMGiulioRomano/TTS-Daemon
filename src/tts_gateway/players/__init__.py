"""Audio output backends and the factory that selects one from configuration."""

from __future__ import annotations

import sys

from tts_gateway.config import PlaybackConfig
from tts_gateway.core.errors import ConfigError
from tts_gateway.core.interfaces import AudioPlayer
from tts_gateway.players.command import CommandPlayer
from tts_gateway.players.null import NullPlayer

__all__ = ["CommandPlayer", "NullPlayer", "create_player"]


def create_player(config: PlaybackConfig) -> AudioPlayer:
    """Build the audio player described by the ``playback`` config section.

    - ``null``: synthesize but never make sound (headless / API-only use).
    - ``command``: always use ``playback.command`` (required).
    - ``auto``: use ``playback.command`` when set; otherwise detect an
      installed playback command, falling back to the standard library
      ``winsound`` module on Windows. When nothing is found the gateway still
      starts -- playback reports unavailable with installation hints, while
      ``/v1/synthesize`` keeps working.
    """
    if config.backend == "null":
        return NullPlayer()

    if config.backend == "command":
        if not config.command:
            raise ConfigError("playback.backend is 'command' but playback.command is not set")
        return CommandPlayer(config.command)

    # backend == "auto"
    if config.command:
        return CommandPlayer(config.command)
    if not CommandPlayer.detect_candidates() and sys.platform == "win32":
        from tts_gateway.players.winsound_player import WinsoundPlayer

        return WinsoundPlayer()
    return CommandPlayer()
