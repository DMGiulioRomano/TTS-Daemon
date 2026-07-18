"""tts-gateway: a local HTTP/WebSocket Text-to-Speech gateway with pluggable providers.

The package is organized in layers:

- :mod:`tts_gateway.core` -- framework-free domain: models, provider/player
  interfaces, the playback queue and the :class:`~tts_gateway.core.service.SpeechService`.
- :mod:`tts_gateway.providers` -- concrete TTS engines (Piper, Tone) and the
  provider registry.
- :mod:`tts_gateway.players` -- audio output backends.
- :mod:`tts_gateway.api` -- the FastAPI HTTP/WebSocket surface.
- :mod:`tts_gateway.config` -- layered configuration (defaults, YAML file,
  environment variables).
- :mod:`tts_gateway.cli` -- the ``tts-gateway`` command line interface.
"""

__version__ = "0.1.0"

from tts_gateway.core.interfaces import AudioPlayer, TTSProvider
from tts_gateway.core.models import (
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
