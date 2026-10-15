#!/usr/bin/env python3
"""Point a GitOps environment at an image: rewrite deploy/gitops/envs/<env>/values.yaml.

    python tools/ci/set_image.py dev --tag sha-1a2b3c4 --digest sha256:<64 hex>

The file holds nothing but the image (tests/test_gitops.py checks that), so it is rewritten whole,
keeping its leading comment.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")
TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


def set_image(env: str, tag: str, digest: str, root: Path = ROOT) -> Path:
    if not DIGEST.match(digest):
        raise ValueError(f"not an image digest: {digest!r}")
    if not TAG.match(tag):
        raise ValueError(f"not an image tag: {tag!r}")
    path = root / "deploy" / "gitops" / "envs" / env / "values.yaml"
    comment = "".join(line for line in path.read_text().splitlines(keepends=True) if line.startswith("#"))
    path.write_text(f'{comment}image:\n  tag: "{tag}"\n  digest: "{digest}"\n')
    return path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("env", choices=["dev", "prod"])
    ap.add_argument("--tag", required=True)
    ap.add_argument("--digest", required=True)
    args = ap.parse_args()
    print(set_image(args.env, args.tag, args.digest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
