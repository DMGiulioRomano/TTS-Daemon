#!/bin/sh
# tts-gateway REST API tour, in curl. Run a gateway first: tts-gateway serve
set -e

GATEWAY="${TTS_GATEWAY_URL:-http://127.0.0.1:5111}"

say() { printf '\n== %s\n' "$*"; }

say "Health check"
curl -s "$GATEWAY/health"; echo

say "Which providers are installed and which is the default?"
curl -s "$GATEWAY/v1/providers"; echo

say "Speak a sentence (returns 202 immediately with the utterance id)"
curl -s -X POST "$GATEWAY/v1/speak" \
  -H 'content-type: application/json' \
  -d '{"text": "Hello from the T T S gateway."}'; echo

say "Queue a second sentence, then interrupt everything with a third"
curl -s -X POST "$GATEWAY/v1/speak" \
  -H 'content-type: application/json' \
  -d '{"text": "This sentence will be cut off."}'; echo
curl -s -X POST "$GATEWAY/v1/speak" \
  -H 'content-type: application/json' \
  -d '{"text": "Priority message coming through.", "interrupt": true}'; echo

say "Block until playback finishes (wait: true) at 1.5x speed"
curl -s -X POST "$GATEWAY/v1/speak" \
  -H 'content-type: application/json' \
  -d '{"text": "Spoken a bit faster.", "speed": 1.5, "wait": true}'; echo

say "Inspect the queue and recent history"
curl -s "$GATEWAY/v1/status"; echo

say "List voices"
curl -s "$GATEWAY/v1/voices"; echo

say "Download audio instead of playing it"
curl -s -X POST "$GATEWAY/v1/synthesize" \
  -H 'content-type: application/json' \
  -d '{"text": "Saved to a file, not played."}' \
  -o example-output.wav
ls -la example-output.wav

say "Stop everything"
curl -s -X POST "$GATEWAY/v1/stop"; echo
