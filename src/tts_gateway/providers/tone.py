"""A dependency-free provider that renders text as a sequence of soft beeps.

It exists for three reasons:

- **Out-of-the-box demo**: the gateway produces audible output on a fresh
  machine with nothing installed, proving the whole pipeline works before a
  real engine (Piper) is set up.
- **Fallback**: with ``default_provider: auto``, the tone provider keeps the
  gateway usable when no real engine is available.
- **Tests**: fast, deterministic audio with no external processes.

One beep is emitted per word; duration scales with word length and the
request's ``speed``, so interruption and queueing behaviour can be exercised
realistically.
"""

from __future__ import annotations

import io
import math
import struct
import wave

from tts_gateway.core.errors import SynthesisError
from tts_gateway.core.interfaces import TTSProvider
from tts_gateway.core.models import AudioClip, AudioFormat, SynthesisRequest, Voice

SAMPLE_RATE = 22_050
AMPLITUDE = 0.30  # peak amplitude in [0, 1]; kept low to be easy on the ears
FADE_SECONDS = 0.008  # short fade in/out per beep to avoid clicks
MAX_BEEPS = 256  # hard cap so a huge text cannot produce minutes of beeping

_VOICES: dict[str, float] = {
    "low": 294.0,  # D4
    "mid": 440.0,  # A4
    "high": 587.0,  # D5
}
_DEFAULT_VOICE = "mid"


class ToneProvider(TTSProvider):
    """Beep synthesizer built entirely on the standard library."""

    name = "tone"

    def synthesize(self, request: SynthesisRequest) -> AudioClip:
        voice_id = request.voice or self.settings.get("default_voice") or _DEFAULT_VOICE
        if voice_id not in _VOICES:
            raise SynthesisError(
                f"Unknown tone voice {voice_id!r} (available: {', '.join(sorted(_VOICES))})"
            )
        if request.speed <= 0:
            raise SynthesisError(f"speed must be positive, got {request.speed}")
        if request.options:
            unknown = ", ".join(sorted(request.options))
            raise SynthesisError(f"The tone provider accepts no options (got: {unknown})")

        base_frequency = _VOICES[voice_id]
        words = request.text.split() or ["."]
        words = words[:MAX_BEEPS]

        samples: list[float] = []
        for index, word in enumerate(words):
            beep_seconds = min(0.10 + 0.02 * len(word), 0.30) / request.speed
            gap_seconds = 0.06 / request.speed
            # Small deterministic pitch wobble per word so longer texts don't
            # sound like a flat alarm.
            frequency = base_frequency * (1.0 + 0.015 * ((index % 5) - 2))
            samples.extend(_beep(frequency, beep_seconds))
            samples.extend([0.0] * int(SAMPLE_RATE * gap_seconds))

        return AudioClip(data=_to_wav(samples), format=AudioFormat.WAV)

    def voices(self) -> list[Voice]:
        return [
            Voice(
                id=voice_id,
                name=f"Tone ({voice_id})",
                language=None,
                description=f"Sine beeps at {frequency:.0f} Hz",
            )
            for voice_id, frequency in sorted(_VOICES.items())
        ]


def _beep(frequency: float, seconds: float) -> list[float]:
    """A sine burst with linear fade in/out."""
    total = max(int(SAMPLE_RATE * seconds), 1)
    fade = min(int(SAMPLE_RATE * FADE_SECONDS), total // 2)
    samples: list[float] = []
    for i in range(total):
        value = AMPLITUDE * math.sin(2.0 * math.pi * frequency * i / SAMPLE_RATE)
        if i < fade:
            value *= i / fade
        elif i >= total - fade and fade > 0:
            value *= (total - 1 - i) / fade
        samples.append(value)
    return samples


def _to_wav(samples: list[float]) -> bytes:
    """Pack float samples in [-1, 1] into a mono 16-bit PCM WAV file."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        frames = bytearray()
        for sample in samples:
            clamped = max(-1.0, min(1.0, sample))
            frames += struct.pack("<h", int(clamped * 32767))
        wav.writeframes(bytes(frames))
    return buffer.getvalue()
