#!/usr/bin/env python3
"""Extract one version's section from CHANGELOG.md (keep-a-changelog format).

Usage: scripts/release_notes.py X.Y.Z [changelog-path]

Prints the body of the ``## [X.Y.Z]`` section (heading excluded, link
definitions excluded) to stdout, for use as GitHub Release notes. Exits
non-zero if the version has no section, so a release cannot ship with an
unwritten changelog.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


def extract(changelog: str, version: str) -> str:
    pattern = re.compile(
        r"^## \[" + re.escape(version) + r"\][^\n]*\n(.*?)(?=^## \[|\Z)",
        re.DOTALL | re.MULTILINE,
    )
    match = pattern.search(changelog)
    if match is None:
        raise SystemExit(f"error: no '## [{version}]' section in CHANGELOG.md")
    body = match.group(1)
    # Drop keep-a-changelog link definition lines ([x.y.z]: https://...).
    body = re.sub(r"^\[[^\]]+\]:\s+\S+\s*$", "", body, flags=re.MULTILINE)
    return body.strip() + "\n"


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__, file=sys.stderr)
        return 2
    version = argv[1].lstrip("v")
    path = Path(argv[2]) if len(argv) == 3 else Path("CHANGELOG.md")
    sys.stdout.write(extract(path.read_text(encoding="utf-8"), version))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
