# Minimal image for the HTTP/WebSocket API and `synthesize`.
# Playback inside a container needs access to the host's audio stack
# (e.g. mount the PulseAudio socket); see docs/installation.md.
FROM python:3.12-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

EXPOSE 5111
CMD ["tts-gateway", "serve", "--host", "0.0.0.0", "--port", "5111"]
