# Contributing

Thanks for helping make tts-gateway better. This project values small,
well-tested changes with clear reasoning.

## Quick start

```sh
git clone https://github.com/DMGiulioRomano/TTS-Gateway.git
cd TTS-Gateway
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
make check        # lint + full test suite — must pass before and after your change
```

The developer handbook ([docs/development.md](docs/development.md)) covers
the repository layout, testing conventions, and style; the design rationale
is in [docs/architecture.md](docs/architecture.md).

## What contributions fit

- **New TTS providers** — the most wanted contribution. Follow
  [docs/providers.md](docs/providers.md). Providers with heavy dependencies
  belong in their own package using the entry-point mechanism; in-tree
  providers must keep the gateway's dependency footprint unchanged.
- **New players / platform support** — same spirit: detection must degrade
  gracefully and error messages must say how to fix the machine.
- **Bug fixes** — please include a regression test that fails without the fix.
- **Docs** — treated with the same care as code.

If you're planning something larger (API changes, new endpoints, streaming),
open an issue first so the design can be discussed before you invest time.

## Ground rules for pull requests

1. **Tests pass and cover the change** (`make check`). New failure paths need
   tests too — error messages users will read are part of the contract.
2. **Keep the public API stable.** `/v1` shapes, `TTSProvider`,
   `AudioPlayer`, and event names only change additively. Anything else
   needs an issue first.
3. **No new runtime dependencies** without prior discussion — the four we
   have (FastAPI, uvicorn, pydantic, PyYAML) are a feature.
4. **Match the house style**: ruff-clean, type hints, docstrings that
   explain *why*, actionable error messages
   ("X not found (install Y, or set Z in the config)").
5. **Update the docs** you invalidate — including `CHANGELOG.md` under
   *Unreleased* — and `config.example.yaml` stays in sync with
   `EXAMPLE_CONFIG` (a test enforces it).
6. One logical change per PR; a clear description of the problem and the
   approach beats a big diff.

## Reporting bugs

Open an issue with: what you did, what you expected, what happened, plus
your OS, Python version, `tts-gateway --version`, and relevant server log
lines (`logging.level: DEBUG` helps). For provider problems, include
`tts-gateway providers` output.

## Security

The gateway is a local, unauthenticated service by design (see the security
note in [docs/installation.md](docs/installation.md)). If you find something
that breaks that model from *outside* the machine, please report it
privately via GitHub security advisories rather than a public issue.

## License

By contributing you agree that your contributions are licensed under the
[MIT License](LICENSE).
