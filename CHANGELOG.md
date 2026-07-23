# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-07-23

### Added

- **Official Docker image** (`Dockerfile`, multi-stage on `python:3.12-slim`):
  the builder installs the gateway and the Piper engine into an isolated venv
  and bakes in one default voice (`en_US-lessac-medium`) at build time; the
  runtime stage copies just the venv and the voice for a lean image that
  synthesizes out of the box. It binds `0.0.0.0` **inside** the container
  (`TTS_DAEMON__SERVER__HOST`; the host port mapping is what keeps it private),
  points Piper at the baked-in voice
  (`TTS_DAEMON__PROVIDERS__PIPER__MODELS_DIR=/data/voices`), and runs in **API
  mode** with playback disabled (`TTS_DAEMON__PLAYBACK__BACKEND=null`) since a
  container has no sound device — `POST /v1/synthesize` returns a WAV. Runs as a
  non-root user, ships a `HEALTHCHECK` against `/health`, and includes
  `libgomp1` (onnxruntime's one system dependency) but no ffmpeg. A
  `docker-compose.yml` example and a Docker section in `docs/installation.md`
  (API mode, keeping it private, and optional host-audio passthrough via
  `--device /dev/snd` or a PulseAudio/PipeWire socket) accompany it. A
  tag-gated GitHub Actions workflow (`docker-publish.yml`, `on: push: tags:
  ['v*']`) builds and pushes the multi-arch image
  (`linux/amd64` + `linux/arm64`) to `ghcr.io/dmgiulioromano/tts-daemon` using
  the built-in `GITHUB_TOKEN` — it never runs on a normal push, so no image is
  published until a release tag is cut.

- **One-line install script, fleshed out** (`scripts/install.sh`): the
  `curl … | sh` installer now checks for Python 3.10+, installs the gateway
  with pipx — or, when pipx is absent, into an isolated venv (which sidesteps
  the "externally managed environment" refusal of `pip install --user` on
  Homebrew/newer-Debian Python), falling back to `pip install --user` only when
  a venv cannot be created — and then _offers_ to install
  the `piper-tts` engine and download a default voice for the user's locale
  (reusing the built-in downloader). It detects an audio playback command and,
  when none is present, prints the exact fix (mirroring `CommandPlayer`'s
  candidate list), then finishes with a "next steps" block. It is POSIX `sh`,
  shellcheck-clean, uses **no `sudo`**, and is idempotent (re-running upgrades in
  place). New flags: `--with-piper`/`--no-piper`, `--voice`/`--no-voice`,
  `--systemd`/`--no-systemd` (optional systemd **user** service on Linux),
  `--from-source`, `--yes`, and `--uninstall` (with `--purge` to also remove
  config, cache and downloaded voices). Prompts read from `/dev/tty` so they work
  under `curl | sh`, and every prompt has an environment-variable equivalent for
  unattended use. A new CI job runs ShellCheck and a full install → re-install →
  uninstall smoke test inside a disposable container (no mutation of the CI
  host).

- **Sentence-level pipelining for long texts** (`speech.chunking`, on by
  default): a long text is split into sentences and the gateway speaks the
  first one while the next synthesizes (look-ahead depth one, on a single-slot
  `ThreadPoolExecutor` owned by the playback worker — no second worker thread),
  so a paragraph starts speaking almost immediately instead of after the whole
  synthesis. It is transparent: one utterance id and the same
  `QUEUED → SYNTHESIZING → SPEAKING → FINISHED` lifecycle, `interrupt`/`stop`
  still cancels all remaining chunks at once, `wait: true` still waits for the
  last sentence, and the provider contract is unchanged (each chunk is a
  complete text in, a complete clip out). Adds an optional `utterance.progress`
  event (`chunk`, `total_chunks`) for UIs. New strict config section
  `speech.chunking` (`enabled`, `min_chars` = 400); disabling it — or a text
  shorter than `min_chars` — restores exactly one clip per utterance. The
  splitter (`core/chunking.py`) is a small, dependency-free regex splitter with
  title/initial guards and a max-length hard-split. `/v1/synthesize` is
  unaffected. No new runtime dependency.
- **openai + elevenlabs providers** (cloud, opt-in, **no new dependency**): two
  premium cloud engines over stdlib `urllib` only — no SDK, no extra — so the
  "local-first with interchangeable engines" story now includes cloud voices
  with a per-request `provider: openai` / `provider: elevenlabs`. `openai` (voice
  `alloy` … from a static list, models `gpt-4o-mini-tts`/`tts-1`/`tts-1-hd`,
  `speed` forwarded only when non-default so `gpt-4o-mini-tts` keeps working);
  `elevenlabs` (`voices()` fetched from the API and cached, empty + logged
  without a key; `options.stability` / `options.similarity_boost` passthrough).
  API key from `providers.<name>.api_key` or `$OPENAI_API_KEY` /
  `$ELEVENLABS_API_KEY`; `availability()` explains the missing key. MP3 output
  (`AudioFormat.MP3`) routed to an MP3-capable player; HTTP errors mapped to
  `SynthesisError` with the API's truncated message; a per-provider
  `timeout_seconds` (default 30) so a hung cloud call cannot wedge the queue.
  Documented as paid cloud providers (privacy/cost note in `docs/providers.md`
  and `docs/configuration.md`); neither is in the default `provider_priority`.
- **kokoro provider** (optional extra `pip install 'tts-daemon[kokoro]'`): a
  small, high-quality **local** neural engine (~82M params) via `kokoro-onnx`
  (ONNX Runtime, CPU-friendly) — offline like piper, but with better prosody.
  Registered as the `kokoro` provider via the entry-point group and
  **lazy-imported**, so the gateway never requires the package. Settings
  `model_path` / `voices_path` / `default_voice` / `lang` are validated lazily;
  `availability()` distinguishes the three failure modes — package missing,
  model file missing, voices file missing — each with the download link.
  `speed` maps to the engine's native speed parameter, `options.lang` overrides
  the language per request (unknown options rejected), and `voices()` lists the
  bundled voice names from the voices file. Output is WAV (a new stdlib-only
  `providers/_audio.floats_to_wav` helper, shared with the tone provider). No
  new runtime dependency.
- **`tts-daemon download kokoro`**: the built-in downloader now fetches the
  kokoro model/voices pair as well as Piper voices. `kokoro` is a reserved
  `download` target (distinct from a Piper voice id) that grabs both
  `kokoro-v1.0.onnx` and `voices-v1.0.bin` from the pinned kokoro-onnx release
  into `~/.local/share/tts-daemon/kokoro/` — respecting the
  `providers.kokoro.model_path` / `voices_path` overrides and `--models-dir`.
  Streams to a `*.part` sidecar and renames atomically, idempotent (skips
  present files, `--force` re-fetches), mirroring the Piper downloader. The
  kokoro release publishes no digest manifest, so only the download's
  `Content-Length` is verified (a truncated file is refused, never installed);
  `docs/configuration.md` now points at the command instead of a manual `curl`.
  Still stdlib `urllib` only — no new runtime dependency.
- **Community-health files**: GitHub issue forms (bug report, feature request,
  provider request), a pull-request template (with a `make check` / docs /
  CHANGELOG checklist), and a Contributor Covenant `CODE_OF_CONDUCT.md`.
  Blank issues are disabled so reports come through the forms; questions are
  routed to Discussions via a contact link.
- **edge-tts provider** (optional extra `pip install 'tts-daemon[edge]'`): free
  Microsoft neural voices — hundreds of languages, no API key, GPU, or model
  download — registered as the `edge` provider via the entry-point group and
  **lazy-imported**, so the gateway never requires the package. `speed` maps to
  the edge rate (`1.5` → `+50%`); `options.pitch` / `options.volume` pass
  through (unknown options rejected); `voices()` come from the package (cached).
  Output is MP3 (`AudioFormat.MP3`), routed to an MP3-capable player
  (ffplay/mpv/afplay). `availability()` explains how to install the package;
  synthesis failures carry an actionable, cloud-aware message. Documented as a
  cloud, unofficial-endpoint provider (privacy note in `docs/providers.md` and
  `docs/configuration.md`); not added to the default `provider_priority`.
- **Built-in Piper voice downloader** (`tts-daemon download <voice>`): fetches a
  voice's `.onnx` model and `.onnx.json` config into the configured `models_dir`
  (created if missing) straight from the `rhasspy/piper-voices` catalog, so
  onboarding no longer needs `python3 -m piper.download_voices`. Downloads are
  verified against the catalog's size and md5 digest, streamed to a `*.part`
  sidecar and renamed atomically, and
  idempotent (already-present voices are skipped; `--force` re-fetches).
  `tts-daemon download --list [--language xx]` browses the catalog (id, language,
  quality, size). Unknown ids suggest the closest matches; offline gives an
  actionable message. New stdlib-only `tts_daemon.voices` module (no new runtime
  deps); the piper `availability()` hint and the README/install quickstart now
  point at the command.
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

### Changed

- **CI now includes a Windows job** (`windows-latest`, Python 3.12) alongside
  Ubuntu and macOS, and the `winsound` fallback player finally has smoke
  coverage (import/selection, the WAV `SND_MEMORY` path, MP3 rejection, error
  mapping, and `SND_PURGE` on stop) — driven through a monkeypatched
  `winsound.PlaySound`, so the Windows runner stays silent and deterministic.
- **Loudness is documented as the OS mixer's job**: there is deliberately no
  request-level `volume` field (requests carry `speed` only; the request models
  reject unknown fields, so `volume` returns 422). Engine-native loudness stays
  available through `options` where a backend supports it (the `edge` provider's
  `options.volume`). Documented in `docs/api.md`; a first-class field will be
  revisited only once ≥2 backends support it natively.
- The Starlette `TestClient` deprecation warning ("use httpx2") is now filtered
  by message so the test run is warning-free; it is external and tracked, to be
  dropped when a Starlette release supports httpx2.

### Fixed

- **WebSocket endpoints now work outside the test suite**: `websockets` is a
  runtime dependency. Plain `uvicorn` ships no WebSocket implementation, so
  `tts-daemon serve` logged "No supported WebSocket library detected" and
  rejected every `/v1/ws` handshake — breaking the playground's live event
  panel. Invisible to the tests because `TestClient` speaks WebSocket
  in-process; a test now asserts the dependency is installed.
- **Test suite is hermetic on a developer machine**, not only on CI. The edge
  provider leaked in through its entry point (its `voices()` really called
  Microsoft) whenever the `[edge]` extra was installed, and piper's default
  `models_dir` picked up voices downloaded into the real
  `~/.local/share/tts-daemon/piper`. Both are now pinned off in the shared
  fixtures, and the "missing edge-tts package" tests stub the import instead of
  assuming the package is absent.

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

[Unreleased]: https://github.com/DMGiulioRomano/TTS-Daemon/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/DMGiulioRomano/TTS-Daemon/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/DMGiulioRomano/TTS-Daemon/releases/tag/v0.1.0
