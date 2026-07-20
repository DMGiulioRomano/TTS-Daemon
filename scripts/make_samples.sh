#!/bin/sh
# Regenerate the audio samples for the GitHub Pages samples site
# (docs/samples/). Keeps the published samples honest: everything on the
# page is produced by `tts-gateway synthesize` with this script.
#
# Usage:  scripts/make_samples.sh [gateway-url]
#
# Requires a running gateway (default http://127.0.0.1:5111). Providers
# that are not available (e.g. Piper without voices installed) are
# skipped with a note, so the script works on a bare checkout too.
set -eu

URL="${1:-http://127.0.0.1:5111}"
OUT="$(dirname "$0")/../docs/samples/audio"
mkdir -p "$OUT"

if ! curl -fsS "$URL/health" >/dev/null 2>&1; then
    echo "error: no gateway at $URL — start one with 'tts-gateway serve'" >&2
    exit 1
fi

# synth <provider> <voice-or-'-'> <outfile> <text>
synth() {
    provider="$1"; voice="$2"; outfile="$3"; text="$4"
    set -- --provider "$provider" -o "$OUT/$outfile" --url "$URL"
    [ "$voice" != "-" ] && set -- "$@" --voice "$voice"
    if tts-gateway synthesize "$@" "$text" 2>/dev/null; then
        echo "  ok    $outfile"
    else
        echo "  skip  $outfile ($provider/$voice not available)"
    fi
}

echo "writing samples to $OUT"

synth tone - tone-beep.wav \
    "Beep beep."

synth piper en_US-lessac-medium piper-en_US-lessac-medium-1.wav \
    "This is the Piper voice speaking through the gateway."
synth piper en_US-lessac-medium piper-en_US-lessac-medium-2.wav \
    "Utterances queue in order, and interrupt cuts in immediately."

synth piper it_IT-paola-medium piper-it_IT-paola-medium-1.wav \
    "Questa è la voce italiana del gateway."
synth piper it_IT-paola-medium piper-it_IT-paola-medium-2.wav \
    "Le frasi vengono lette in coda, una dopo l'altra."

echo "done — open docs/samples/index.html"
