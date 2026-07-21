# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Optional bearer-token authentication** (`server.auth_token`, env
  `TTS_DAEMON__SERVER__AUTH_TOKEN`): off by default (unauthenticated on
  loopback). When set, every `/v1` route requires `Authorization: Bearer
  <token>`; `/v1/ws` and `/v1/events` also accept a `?token=` query param
  (browsers can't set headers on `WebSocket`/`EventSource`). `GET /health` and
  the playground at `/` stay open. Tokens are compared in constant time
  (`secrets.compare_digest`); a wrong/missing token returns `401` with the
  standard `{"detail": …}` body. Wired as a FastAPI dependency on the `/v1`
  router, so the scheme is documented in the OpenAPI schema. The gateway logs a
  warning at startup when `server.host` is not loopback and no token is set. The
  Python client and CLI gain a `token` argument / `--token` flag
  (`TTS_DAEMON_TOKEN` env); the playground prompts for a token and stores it in
  `localStorage`; the browser userscript (a `TOKEN` constant) and the Claude
  Code hook (`TTS_DAEMON_TOKEN` env) can send it too.
- **Web playground at `/`**: the root page is now an interactive playground
  instead of a static info page. Type text, pick a provider and voice
  (populated live from `/v1/providers` and `/v1/voices`, unavailable providers
  shown disabled with their reason), set speed, then **Speak** (server-side),
  **Play here** (in-browser via `/v1/synthesize`), or **Stop** — with a live
  panel of the current utterance, queue, and lifecycle events over WebSocket.
  Single self-contained HTML file (inline CSS/JS, zero build, zero external
  requests, dark/light aware), shipped as package data.

- **SSE events endpoint** (`GET /v1/events`): the live gateway event stream as
  Server-Sent Events, consumable with plain `curl -N` or a browser
  `EventSource` — no WebSocket client needed. Supports a `?types=` filter,
  emits a `: ping` heartbeat every ~15 s, and reuses the WebSocket's
  snapshot/slow-consumer semantics. The thread→asyncio event bridge is now a
  shared `api/event_bridge.py` helper used by both the WebSocket and SSE.
- **On-disk synthesis cache**: repeated phrases replay instantly instead of
  re-synthesizing. A size-bounded LRU store under `$XDG_CACHE_HOME/tts-daemon`
  keyed by provider, voice, speed, options, text, and a provider fingerprint
  (piper folds in the voice model's mtime so a swapped model invalidates
  cached clips). New `cache` config section (`enabled`, `max_mb`, `dir`; on by
  default), per-request `no_cache` option to bypass, and cache stats
  (`entries`, `size_mb`, `hits`, `misses`) in `GET /v1/status`. Atomic writes
  and corruption-tolerant (a broken cache is a miss, never an error).
- **OpenAI-compatible endpoint** (`POST /v1/audio/speech`): a drop-in for
  OpenAI's speech API, so any OpenAI TTS client synthesizes locally by pointing
  its `base_url` at the gateway. Maps `model` (generic names → default provider,
  a provider name selects it), `voice` (via a new `openai_compat.voice_aliases`
  config section, falling back to the provider default), and `speed`; `wav`
  response format supported (others → 422). Runnable `examples/openai_compat.py`
  using the official `openai` client.

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
