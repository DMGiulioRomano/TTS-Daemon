#!/usr/bin/env python3
"""Using the bundled GatewayClient from Python.

Run a gateway first (``tts-gateway serve``), then::

    python3 examples/python_client.py
"""

from __future__ import annotations

from tts_gateway.client import GatewayClient, GatewayClientError


def main() -> int:
    client = GatewayClient()  # defaults to http://127.0.0.1:5111

    try:
        health = client.health()
    except GatewayClientError as exc:
        print(exc)
        return 1
    print(f"gateway v{health['version']} is up")

    providers = client.providers()["providers"]
    default = next((p["name"] for p in providers if p["default"]), None)
    print(f"default provider: {default}")
    for provider in providers:
        state = "ok" if provider["available"] else f"unavailable: {provider['reason']}"
        print(f"  - {provider['name']}: {state}")

    print("queueing two utterances...")
    client.speak("First sentence, spoken in order.")
    result = client.speak("Second sentence, waited for.", wait=True)
    print(f"final state: {result['utterance']['state']}")

    print("interrupting demo: a long sentence cut off by a short one")
    client.speak("This very long sentence is going to be interrupted before it can finish.")
    client.speak("Interrupted!", interrupt=True, wait=True)

    audio = client.synthesize("Audio bytes without playback.")
    print(f"synthesize returned {len(audio)} bytes of WAV")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
