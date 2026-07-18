# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-07-18

First release.

### Added

- **Server**: FastAPI HTTP + WebSocket gateway (`tts-gateway serve`),
  binding to `127.0.0.1:5111` by default.
- **REST API** (`/v1`): `speak` (queue, `interrupt`, `wait`), `synthesize`
  (audio bytes without playback), `stop`, `status`, `utterances/{id}`,
  `voices`, `providers`; plus `/health` and an HTML index at `/`.
- **WebSocket** (`/v1/ws`): `speak`/`stop`/`status`/`ping` commands with
  correlation ids and a live stream of utterance lifecycle events.
- **Playback queue**: single-worker FIFO with prompt interruption,
  per-utterance state machine (`queued → synthesizing → speaking →
  finished/cancelled/failed`), bounded size, and recent history.
- **Provider architecture**: `TTSProvider` interface, registry with lazy
  instantiation, per-provider config sections, `auto` default-provider
  resolution by availability, and third-party discovery via the
  `tts_gateway.providers` entry point group.
- **Piper provider**: subprocess integration with voice model discovery,
  per-request voice/speed/speaker, configurable binary, models dir, speed
  flag, timeout, and extra args.
- **Tone provider**: dependency-free beep synthesizer so the gateway works
  (and is testable) with no TTS engine installed.
- **Players**: auto-detected command playback (`pw-play`, `paplay`, `aplay`,
  `ffplay`, `mpv`, `play`, `afplay`) with format-aware selection and
  process-group interruption; `null` backend; Windows `winsound` fallback.
- **Configuration**: layered defaults ← YAML file ← `TTS_GATEWAY__*` env
  vars, strict validation, annotated template via `tts-gateway init-config`.
- **CLI**: `serve`, `speak`, `synthesize`, `stop`, `status`, `voices`,
  `providers`, `init-config` (client commands are stdlib-only).
- **Integrations**: browser userscript (speak selection, Alt+S/Alt+X) and a
  Claude Code hook that speaks replies and notifications.
- **Examples**: curl tour, Python client, WebSocket event listener.
- **Docs**: installation, configuration, API reference, architecture,
  provider-writing guide, development guide, contributing guide.
- **Tests**: 188 unit + integration tests (queue concurrency, fake-piper
  subprocess suite, full REST/WebSocket contract).

[Unreleased]: https://github.com/DMGiulioRomano/TTS-Gateway/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/DMGiulioRomano/TTS-Gateway/releases/tag/v0.1.0
