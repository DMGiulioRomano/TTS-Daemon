# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Built-in Piper voice downloader** (#11): `tts-daemon download <voice>`
  fetches a voice's `.onnx` + `.onnx.json` into the models directory (atomic,
  idempotent, `--force` to refetch, progress bar with size verification), and
  `tts-daemon download --list [--language xx]` browses the Hugging Face
  `rhasspy/piper-voices` catalog. Unknown ids suggest close matches; offline
  and incomplete-download failures are actionable. Pure stdlib (`voices.py`).
- **edge-tts provider** (#15): free Microsoft neural voices with no API key,
  GPU, or model downloads — the shortest path to a real voice. Optional extra
  `pip install 'tts-daemon[edge]'`, lazily imported so the gateway never
  requires it. Maps `speed` to edge's rate, passes `pitch`/`volume` options
  through, lists voices from the package, and outputs MP3. Cloud/unofficial-API
  caveats documented in `docs/providers.md`.
- **OpenAI-compatible endpoint** `POST /v1/audio/speech` (#10): a drop-in for
  OpenAI's speech API — point any OpenAI TTS client's `base_url` at the gateway
  for local voices. Maps `model`/`voice`/`speed`, honors a registered provider
  name as `model`, resolves OpenAI voice names via an `openai_compat.voice_aliases`
  config section, returns WAV (other `response_format`s → 422). Example in
  `examples/openai_compat.py`.
- **On-disk synthesis cache** (#13): repeated phrases replay from a
  content-addressed cache (`$XDG_CACHE_HOME/tts-daemon`) instead of being
  re-synthesized. Configurable `cache: {enabled, max_mb}` with size-based LRU
  eviction, atomic writes, corruption-as-miss recovery, per-request
  `options: {"no_cache": true}` bypass, and `cache` stats in `GET /v1/status`.
  Providers can refine the key via `TTSProvider.cache_fingerprint` (piper mixes
  in its model file mtime so swapping a model invalidates the cache).
- **Server-Sent Events endpoint** `GET /v1/events` (#14): the live gateway
  event stream over SSE (native `EventSource` / `curl -N`), with an optional
  `?types=` filter and comment heartbeats. The thread→asyncio event bridge is
  now shared between the WebSocket and SSE endpoints
  (`api/event_bridge.py`).
- **Community health files** (#7): YAML issue forms for bug reports, feature
  requests, and provider requests (`.github/ISSUE_TEMPLATE/`), a pull-request
  template, and a Contributor Covenant `CODE_OF_CONDUCT.md`.

## [0.1.0] - 2026-07-21

First public release.

### Added

- **Server**: FastAPI HTTP + WebSocket gateway (`tts-daemon serve`),
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
  `tts_daemon.providers` entry point group.
- **Piper provider**: subprocess integration with voice model discovery,
  per-request voice/speed/speaker, configurable binary, models dir, speed
  flag, timeout, and extra args.
- **Tone provider**: dependency-free beep synthesizer so the gateway works
  (and is testable) with no TTS engine installed.
- **Players**: auto-detected command playback (`pw-play`, `paplay`, `aplay`,
  `ffplay`, `mpv`, `play`, `afplay`) with format-aware selection and
  process-group interruption; `null` backend; Windows `winsound` fallback.
- **Configuration**: layered defaults ← YAML file ← `TTS_DAEMON__*` env
  vars, strict validation, annotated template via `tts-daemon init-config`.
- **CLI**: `serve`, `speak`, `synthesize`, `stop`, `status`, `voices`,
  `providers`, `init-config` (client commands are stdlib-only).
- **Integrations**: browser userscript (speak selection, Alt+S/Alt+X) and a
  Claude Code hook that speaks replies and notifications.
- **Examples**: curl tour, Python client, WebSocket event listener.
- **Docs**: installation, configuration, API reference, architecture,
  provider-writing guide, development guide, contributing guide.
- **Tests**: 188 unit + integration tests (queue concurrency, fake-piper
  subprocess suite, full REST/WebSocket contract).

- README rebuilt as a conversion funnel: centered logo (new `assets/`
  wordmark, light + dark variants), badge row, install-first layout
  (pip/pipx, one-line script, Docker), a "60-second tour" with real
  outputs, a comparison table vs. raw piper / speech-dispatcher / cloud
  APIs, and a star-history chart.
- `Dockerfile` (API + synthesis in a container) and
  `scripts/install.sh` one-line installer.
- Demo assets: `assets/demo.gif` shown in the README, with a committed
  VHS script (`assets/demo.tape`) so it can be regenerated after UI
  changes; a second `assets/hook.gif` (+ `assets/hook.tape`) showing the
  Claude Code hook in the integrations section; audio samples page for
  GitHub Pages (`docs/samples/`) with a reproducible generation script
  (`scripts/make_samples.sh`).
- Release automation: pushing a `v*` tag now builds the package, publishes
  it to PyPI via trusted publishing (OIDC), and creates a GitHub Release
  with notes extracted from this changelog (`.github/workflows/release.yml`,
  `scripts/release_notes.py`); a manual run publishes to TestPyPI as a dry
  run. `scripts/check_version.py` enforces that `pyproject.toml` and
  `tts_daemon.__version__` agree (and match the tag).
- Browser userscript: **auto-read** of new chat replies (`Alt+A` toggles it
  per site, remembered across reloads). A `MutationObserver` reads each new
  assistant message once it stops streaming, skipping code blocks; existing
  history is baselined silent. The assistant-message CSS selector has
  built-in defaults for ChatGPT/Claude/Gemini and is configurable per site
  from the userscript menu for any other chat.

[Unreleased]: https://github.com/DMGiulioRomano/TTS-Daemon/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/DMGiulioRomano/TTS-Daemon/releases/tag/v0.1.0
