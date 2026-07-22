# Multi-stage image for the TTS Daemon in API mode.
#
# The builder installs the gateway + the Piper engine into a self-contained
# virtualenv and bakes in one default voice (en_US-lessac-medium), so the image
# synthesizes speech out of the box with no download on first run. The runtime
# stage copies just that virtualenv and the voice, giving a lean image with no
# build tools or source tree.
#
# Containers have no sound device, so the image runs in API mode: POST
# /v1/synthesize returns a WAV, and playback stays off (TTS_DAEMON__PLAYBACK__
# BACKEND=null). To actually hear audio you must pass the host's audio device
# in and flip the playback backend back on — see docs/installation.md#docker.

# --------------------------------------------------------------- builder
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# An isolated venv we can copy wholesale into the runtime stage.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /src
COPY . .

# The gateway (from this source tree) plus the Piper engine.
RUN pip install . piper-tts

# Bake a default voice into the image (downloaded once, at build time).
RUN tts-daemon download en_US-lessac-medium --models-dir /data/voices

# --------------------------------------------------------------- runtime
FROM python:3.12-slim AS runtime

# curl for the HEALTHCHECK; libgomp1 is onnxruntime's one runtime system dep
# (Piper). No ffmpeg — API mode needs no audio player.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /data/voices /data/voices

ENV PATH="/opt/venv/bin:$PATH" \
    # Bind 0.0.0.0 *inside* the container; the host port mapping is what keeps
    # the gateway private (see the compose file / docs).
    TTS_DAEMON__SERVER__HOST=0.0.0.0 \
    # Use the voice baked in above.
    TTS_DAEMON__PROVIDERS__PIPER__MODELS_DIR=/data/voices \
    # API mode: synthesize but never try to play (no sound device in a container).
    TTS_DAEMON__PLAYBACK__BACKEND=null

# Drop privileges; give the app a home for the on-disk synthesis cache.
RUN useradd --create-home --uid 10001 app && chown -R app:app /data
USER app

EXPOSE 5111

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:5111/health || exit 1

CMD ["tts-daemon", "serve"]
