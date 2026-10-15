"""Render the ApplicationSet for an end-to-end test: one environment, a given commit, a locally loaded image.

    python e2e/render_appset.py --revision <sha> --env dev --image podcast-agent:e2e | kubectl apply -f -

Production reads main and pulls digests from GHCR; on a throwaway kind cluster there is no published
image for an unmerged commit, so the test overrides the image with one built and loaded into kind.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

APPSET = Path(__file__).resolve().parents[1] / "deploy" / "gitops" / "argocd" / "applicationset.yaml"


def render(revision: str, env: str, image: str) -> dict:
    doc = yaml.safe_load(APPSET.read_text(encoding="utf-8"))
    gen = doc["spec"]["generators"][0]["list"]
    gen["elements"] = [e for e in gen["elements"] if e["env"] == env]
    if not gen["elements"]:
        raise SystemExit(f"no environment {env!r} in {APPSET}")
    source = doc["spec"]["template"]["spec"]["source"]
    source["targetRevision"] = revision
    repo, _, tag = image.rpartition(":")
    source["helm"]["parameters"] = [
        {"name": "image.repository", "value": repo},
        {"name": "image.tag", "value": tag},
        {"name": "image.digest", "value": ""},
        {"name": "image.pullPolicy", "value": "Never"},
    ]
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--revision", required=True, help="commit SHA or branch Argo CD should deploy")
    ap.add_argument("--env", default="dev")
    ap.add_argument("--image", default="podcast-agent:e2e", help="repository:tag already loaded into the cluster")
    args = ap.parse_args()
    yaml.safe_dump(render(args.revision, args.env, args.image), sys.stdout, sort_keys=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
