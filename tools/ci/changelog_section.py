#!/usr/bin/env python3
"""Print one version's section of CHANGELOG.md, for the GitHub Release body.

    python tools/ci/changelog_section.py 0.2.0 > notes.md
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def section(version: str, text: str) -> str:
    match = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## \[|^\[[^\]]+\]: )", text, re.M | re.S)
    if not match:
        raise LookupError(f"CHANGELOG.md has no ## [{version}] section")
    return match.group(1).strip() + "\n"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    sys.stdout.write(section(sys.argv[1].removeprefix("v"), (ROOT / "CHANGELOG.md").read_text()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
