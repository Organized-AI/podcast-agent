#!/usr/bin/env python3
"""Check that every place that states the version agrees.

    python tools/ci/check_version.py              # pyproject == __version__ == Chart appVersion
    python tools/ci/check_version.py --tag v0.2.0  # also: the tag matches and CHANGELOG has the section
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def versions() -> dict[str, str]:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    init = re.search(r'__version__ = "([^"]+)"', (ROOT / "src/podcast_agent/__init__.py").read_text())
    chart = re.search(r'^appVersion:\s*"?([^"\n]+)"?', (ROOT / "deploy/helm/podcast-agent/Chart.yaml").read_text(), re.M)
    return {"pyproject.toml": pyproject, "__version__": init.group(1) if init else "?",
            "Chart.yaml appVersion": chart.group(1).strip() if chart else "?"}


def main() -> int:
    found = versions()
    problems = []
    if len(set(found.values())) != 1:
        problems.append("versions disagree: " + ", ".join(f"{k}={v}" for k, v in found.items()))
    version = found["pyproject.toml"]
    if "--tag" in sys.argv:
        tag = sys.argv[sys.argv.index("--tag") + 1]
        if tag.removeprefix("v") != version:
            problems.append(f"tag {tag} does not match version {version}")
        if f"## [{version}]" not in (ROOT / "CHANGELOG.md").read_text():
            problems.append(f"CHANGELOG.md has no ## [{version}] section")
    for p in problems:
        print(p)
    if not problems:
        print(f"version {version} agrees everywhere")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
