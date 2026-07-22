"""Small audio helpers shared by providers that emit raw float samples.

Kept dependency-free (standard-library ``wave`` only): a provider that already
holds decoded samples in memory — the tone beeper, or a neural engine like
kokoro that returns numpy floats — packs them into a playable clip without the
gateway growing an audio dependency.
"""

from __future__ import annotations

import io
import struct
import wave
from collections.abc import Iterable

_INT16_MAX = 32767


def floats_to_wav(samples: Iterable[float], sample_rate: int) -> bytes:
    """Pack mono float samples in ``[-1, 1]`` into a 16-bit PCM WAV file.

    ``samples`` may be any iterable of numbers (a Python list, or a numpy
    array via ``.tolist()``); values are clamped before conversion so a hot
    engine that overshoots slightly cannot produce clipping artefacts that
    wrap around. ``sample_rate`` is written into the WAV header verbatim.
    """
    if sample_rate <= 0:
        raise ValueError(f"sample_rate must be positive, got {sample_rate}")
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        frames = bytearray()
        for sample in samples:
            clamped = max(-1.0, min(1.0, float(sample)))
            frames += struct.pack("<h", int(clamped * _INT16_MAX))
        wav.writeframes(bytes(frames))
    return buffer.getvalue()
