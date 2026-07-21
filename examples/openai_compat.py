#!/usr/bin/env python3
"""Drive the gateway with the official OpenAI Python client.

The gateway exposes ``POST /v1/audio/speech`` with OpenAI's schema, so any
OpenAI TTS client works by pointing ``base_url`` at it — no API key, no cloud,
audio synthesized locally by whichever provider the gateway is configured with.

Run a gateway first (``tts-daemon serve``), then::

    pip install openai
    python3 examples/openai_compat.py

Note: OpenAI clients default ``response_format`` to mp3; the gateway currently
returns WAV, so this example asks for ``wav`` explicitly.
"""

from __future__ import annotations

from pathlib import Path

try:
    from openai import OpenAI
except ImportError:
    raise SystemExit("This example needs the OpenAI client: pip install openai") from None


def main() -> int:
    # api_key is unused by the gateway but the client requires a non-empty value.
    client = OpenAI(base_url="http://127.0.0.1:5111/v1", api_key="unused")

    output = Path("openai_compat_output.wav")
    response = client.audio.speech.create(
        model="tts-1",  # tts-1/tts-1-hd -> gateway default provider; or use "piper"
        voice="alloy",  # mapped via openai_compat.voice_aliases, else provider default
        input="This audio was synthesized locally through an OpenAI-compatible endpoint.",
        response_format="wav",
        speed=1.0,
    )
    response.stream_to_file(output)
    print(f"wrote {output} ({output.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
