# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-07-20

First PyPI release (distribution `tts-daemon`).

### Added

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
  `tts_gateway.__version__` agree (and match the tag).
- Browser userscript: **auto-read** of new chat replies (`Alt+A` toggles it
  per site, remembered across reloads). A `MutationObserver` reads each new
  assistant message once it stops streaming, skipping code blocks; existing
  history is baselined silent. The assistant-message CSS selector has
  built-in defaults for ChatGPT/Claude/Gemini and is configurable per site
  from the userscript menu for any other chat.

### Changed

- The PyPI distribution is named `tts-daemon` (`pip install tts-daemon`) —
  the `tts-gateway` name on PyPI is taken by an unrelated project. The
  import package (`tts_gateway`) and the `tts-gateway` command are
  unchanged.

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

[Unreleased]: https://github.com/DMGiulioRomano/TTS-Gateway/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/DMGiulioRomano/TTS-Gateway/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/DMGiulioRomano/TTS-Gateway/releases/tag/v0.1.0
