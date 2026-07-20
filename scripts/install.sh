#!/bin/sh
# One-line installer for tts-gateway:
#   curl -fsSL https://raw.githubusercontent.com/DMGiulioRomano/TTS-Gateway/main/scripts/install.sh | sh
#
# Prefers pipx (isolated install); falls back to `pip install --user`.
# The PyPI distribution is named `tts-daemon`; the command it installs
# is `tts-gateway`.
set -eu

PKG=tts-daemon

if command -v pipx >/dev/null 2>&1; then
    echo ">> installing with pipx"
    pipx install "$PKG"
elif command -v python3 >/dev/null 2>&1; then
    echo ">> pipx not found; installing with pip --user"
    python3 -m pip install --user "$PKG"
    echo ">> make sure your user script dir (python3 -m site --user-base)/bin is on PATH"
else
    echo "error: python3 is required (3.10+). Install it and re-run." >&2
    exit 1
fi

echo ">> done. Try:  tts-gateway serve"
