"""tts-daemon: a local HTTP/WebSocket Text-to-Speech gateway with pluggable providers.

The package is organized in layers:

- :mod:`tts_daemon.core` -- framework-free domain: models, provider/player
  interfaces, the playback queue and the :class:`~tts_daemon.core.service.SpeechService`.
- :mod:`tts_daemon.providers` -- concrete TTS engines (Piper, Tone) and the
  provider registry.
- :mod:`tts_daemon.players` -- audio output backends.
- :mod:`tts_daemon.api` -- the FastAPI HTTP/WebSocket surface.
- :mod:`tts_daemon.config` -- layered configuration (defaults, YAML file,
  environment variables).
- :mod:`tts_daemon.cli` -- the ``tts-daemon`` command line interface.
"""

__version__ = "0.2.0"

from tts_daemon.core.interfaces import AudioPlayer, TTSProvider
from tts_daemon.core.models import (
    AudioClip,
    AudioFormat,
    Availability,
    SynthesisRequest,
    Utterance,
    UtteranceState,
    Voice,
)

__all__ = [
    "AudioClip",
    "AudioFormat",
    "AudioPlayer",
    "Availability",
    "SynthesisRequest",
    "TTSProvider",
    "Utterance",
    "UtteranceState",
    "Voice",
    "__version__",
]
