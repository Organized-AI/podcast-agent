"""Render the ApplicationSet for one environment on a test or lab cluster.

    python e2e/render_appset.py --revision <sha> --env dev --image podcast-agent:e2e | kubectl apply -f -
    python e2e/render_appset.py --revision main --env dev --set api.service.type=NodePort | kubectl apply -f -

With --image (the throwaway e2e cluster): an unmerged commit has no published image, so the test overrides
the image with one built and loaded into kind. Without --image (the standing lab cluster): the image comes
from Git, deploy/gitops/envs/<env>/values.yaml, exactly as in production; --set only adds Helm parameters
that do not touch the image, such as how the lab reaches the API.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

APPSET = Path(__file__).resolve().parents[1] / "deploy" / "gitops" / "argocd" / "applicationset.yaml"


def render(revision: str, env: str, image: str | None = None, params: dict[str, str] | None = None) -> dict:
    doc = yaml.safe_load(APPSET.read_text(encoding="utf-8"))
    gen = doc["spec"]["generators"][0]["list"]
    gen["elements"] = [e for e in gen["elements"] if e["env"] == env]
    if not gen["elements"]:
        raise SystemExit(f"no environment {env!r} in {APPSET}")
    source = doc["spec"]["template"]["spec"]["source"]
    source["targetRevision"] = revision
    extra = dict(params or {})
    if any(k.startswith("image.") for k in extra):
        raise SystemExit("--set cannot change the image; it comes from Git (or --image on a test cluster)")
    parameters = []
    if image is not None:
        repo, _, tag = image.rpartition(":")
        parameters = [
            {"name": "image.repository", "value": repo},
            {"name": "image.tag", "value": tag},
            {"name": "image.digest", "value": ""},
            {"name": "image.pullPolicy", "value": "Never"},
        ]
    parameters += [{"name": k, "value": v} for k, v in extra.items()]
    if parameters:
        source["helm"]["parameters"] = parameters
    return doc


def _pair(text: str) -> tuple[str, str]:
    key, sep, value = text.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"expected key=value, got {text!r}")
    return key, value


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--revision", required=True, help="commit SHA or branch Argo CD should deploy")
    ap.add_argument("--env", default="dev")
    ap.add_argument("--image", help="repository:tag already loaded into the cluster (test clusters only)")
    ap.add_argument("--set", dest="params", action="append", type=_pair, default=[], metavar="KEY=VALUE",
                    help="extra Helm parameter, repeatable; image.* is refused")
    args = ap.parse_args()
    yaml.safe_dump(render(args.revision, args.env, args.image, dict(args.params)), sys.stdout, sort_keys=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
