#!/usr/bin/env python3
"""Live gateway events over WebSocket.

Needs the ``websockets`` package (the only example with a dependency)::

    pip install websockets
    python3 examples/websocket_client.py "Something to say"

Speaks the given text, then prints every gateway event until the utterance
reaches a terminal state — a template for building "now speaking" UIs.
"""

from __future__ import annotations

import asyncio
import json
import sys

try:
    import websockets
except ImportError:  # pragma: no cover - example-only dependency
    print("This example needs: pip install websockets", file=sys.stderr)
    sys.exit(1)

GATEWAY_WS = "ws://127.0.0.1:5111/v1/ws"
TERMINAL_STATES = {"finished", "cancelled", "failed"}


async def main(text: str) -> None:
    async with websockets.connect(GATEWAY_WS) as ws:
        await ws.send(json.dumps({"type": "speak", "text": text, "id": 1}))

        utterance_id = None
        while True:
            message = json.loads(await ws.recv())

            if message["type"] == "result" and message["id"] == 1:
                utterance_id = message["data"]["utterance"]["id"]
                print(f"queued as {utterance_id}")
            elif message["type"] == "error":
                print(f"error: {message['detail']}", file=sys.stderr)
                return
            elif message["type"] == "event":
                event = message["event"]
                data = event["data"]
                if data.get("id") != utterance_id:
                    continue  # someone else's utterance
                print(f"{event['type']:<24} state={data['state']}")
                if data["state"] in TERMINAL_STATES:
                    return


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or "Hello over websocket."))
