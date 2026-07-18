# Development guide

## Setup

```sh
git clone https://github.com/DMGiulioRomano/TTS-Gateway.git
cd TTS-Gateway
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

`-e` (editable) means source edits are live without reinstalling. The `dev`
extra adds pytest, httpx (for FastAPI's TestClient), and ruff.

## Everyday commands

```sh
make test          # full suite (fast: ~3s, no network, no sound)
make lint          # ruff check + format --check
make format        # auto-fix imports/style
make serve         # run a dev server (tone provider works out of the box)
make check         # lint + test: what CI runs
```

Without make: `python3 -m pytest`, `ruff check src tests`,
`ruff format src tests`.

### Live-reload server

```sh
uvicorn --factory tts_gateway.api.app:create_app --reload --port 5111
```

### Trying real audio locally

The test suite never makes sound. To hear something:

```sh
tts-gateway serve                       # terminal 1
tts-gateway speak "beep beep" # terminal 2 — tone provider, no engine needed
```

For real speech install Piper ([installation.md](installation.md)).

## Repository map

```
src/tts_gateway/
├── core/            framework-free domain: models, interfaces, queue,
│                    service, events, errors
├── providers/       TTS engines + registry (entry-point discovery)
├── players/         audio output backends
├── api/             FastAPI app, REST routes, WebSocket, DTOs
├── config.py        layered configuration (defaults ← YAML ← env)
├── client.py        stdlib-only HTTP client (used by the CLI)
├── cli.py           argparse CLI
└── defaults.py      dependency-free host/port constants
tests/
├── unit/            one file per core module; fake piper binary; doubles
└── integration/     TestClient API + WebSocket + CLI + hook script
docs/                this documentation
integrations/        browser userscript, Claude Code hook
examples/            curl, Python client, WebSocket client
```

Start reading at `core/interfaces.py` (the two ports), then `core/queue.py`
(the threading heart), then `api/app.py` (how it all gets wired). The
architecture rationale is in [architecture.md](architecture.md).

## Testing conventions

- Tests must stay **fast, silent, and hermetic**: no network, no audio
  devices, no reads of the developer's real `~/.config`. Config in tests is
  built via `tests/conftest.py:make_config`; env-dependent tests pass an
  explicit `env=` mapping.
- Concurrency is tested with the controllable doubles in `conftest.py`
  (`ControllablePlayer`, `BlockingProvider`) — never with `sleep`-and-hope.
  If you need to wait, wait on an event with a timeout.
- Subprocess behaviour (piper, players) is tested against tiny shell
  scripts, not mocks of `subprocess`, so real argv/stdin/exit-code handling
  is covered.
- New features need tests for the failure paths, not just the happy path;
  the error message text is part of the contract when users will read it.

## Style

- `ruff` is the single authority for lint + formatting (line length 100);
  CI enforces `ruff check` and `ruff format --check`.
- Type hints everywhere; `from __future__ import annotations` at the top of
  every module.
- Docstrings explain *why* and the contract, not what the next line does.
- Error messages must say how to fix the problem — grep the codebase for
  `Availability.unavailable` to see the tone.

## Release checklist

1. Update `__version__` in `src/tts_gateway/__init__.py` and the
   `[project]` version in `pyproject.toml` (keep them equal).
2. Move `CHANGELOG.md` *Unreleased* entries under the new version + date.
3. `make check` on a clean tree.
4. Tag: `git tag -a v0.x.0 -m "v0.x.0" && git push --tags`.
5. Build & publish: `python3 -m build && twine upload dist/*`.
