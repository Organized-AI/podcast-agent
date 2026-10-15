"""Synthetic canary: send one known episode through the API and report what it cost.

    podcast-agent canary --url http://podcast-agent-api          # dev runs this every minute as a CronJob

The canary episode (data/canary/canary_roundtable.json) is a long roundtable with more check-worthy claims than the
default claim budget allows, so it is the first episode to get more expensive when someone raises a budget. Its id
starts with "canary", which tags its metrics and spans podcast_agent.traffic="canary": an overrun on the canary alone
means no customer episode has gone over yet. Exit status 1 when the API fails or returns no fact checks.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import httpx

DEFAULT_TRANSCRIPT = "data/canary/canary_roundtable.json"


def run(url: str, transcript: Path, timeout_s: float = 90.0) -> dict[str, Any]:
    body = {"transcript": json.loads(transcript.read_text(encoding="utf-8"))}
    resp = httpx.post(f"{url.rstrip('/')}/v1/process", json=body, timeout=timeout_s)
    resp.raise_for_status()
    report = resp.json()["report"]
    usage = report["usage"]
    return {
        "episode": report["episode"]["episode_id"],
        "claims": len(report["fact_checks"]),
        "llm_calls": usage["llm_calls"],
        "cost_usd": usage.get("cost_usd"),
        "budget_usd": usage.get("budget_usd"),
        "over_budget": usage.get("over_budget"),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="podcast-agent canary", description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", required=True, help="the API's base URL")
    ap.add_argument("--transcript", type=Path, default=Path(DEFAULT_TRANSCRIPT))
    ap.add_argument("--timeout", type=float, default=90.0, help="seconds to wait for the episode")
    args = ap.parse_args(argv)
    try:
        result = run(args.url, args.transcript, args.timeout)
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"}))
        return 1
    print(json.dumps({"ok": result["claims"] > 0, **result}))
    return 0 if result["claims"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
