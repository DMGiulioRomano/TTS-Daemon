#!/usr/bin/env python3
"""Fail if the project's version is not single-sourced consistently.

Usage: scripts/check_version.py [expected-version]

Compares the ``[project] version`` in pyproject.toml with
``tts_gateway.__version__`` (read statically, no import needed), and — when
an expected version is given (e.g. the git tag on a release) — checks both
against it. Exits non-zero on any mismatch.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import tomllib


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = pyproject["project"]["version"]

    init_text = (root / "src" / "tts_gateway" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'^__version__ = "([^"]+)"$', init_text, re.MULTILINE)
    if match is None:
        print("error: __version__ not found in src/tts_gateway/__init__.py", file=sys.stderr)
        return 1
    init_version = match.group(1)

    if project_version != init_version:
        print(
            f"error: version mismatch: pyproject.toml has {project_version}, "
            f"tts_gateway.__version__ is {init_version}",
            file=sys.stderr,
        )
        return 1

    if len(argv) > 1:
        expected = argv[1].lstrip("v")
        if project_version != expected:
            print(
                f"error: version {project_version} does not match expected {expected} "
                "(did you forget to bump before tagging?)",
                file=sys.stderr,
            )
            return 1

    print(f"version ok: {project_version}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
