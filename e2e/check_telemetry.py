"""End-to-end check: a run's trace reached Jaeger and its metrics reached Prometheus.

    python e2e/check_telemetry.py output/observe --jaeger http://jaeger:16686 --prometheus http://prometheus:9090

Reads the run folder (run_manifest.json, report.json), then polls the backends until everything the run
produced is there or the timeout passes. Exits non-zero, with a line per failed check, if anything is missing.
Standard library only, so it runs inside the agent image on the Compose network.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from typing import Any

CONTENT_ATTRIBUTES = {"gen_ai.input.messages", "gen_ai.output.messages", "gen_ai.tool.call.arguments"}


def get_json(url: str) -> Any:
    with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310 - URLs come from the command line
        return json.load(resp)


def prom_value(base: str, query: str) -> float:
    data = get_json(f"{base}/api/v1/query?{urllib.parse.urlencode({'query': query})}")["data"]["result"]
    return float(data[0]["value"][1]) if data else 0.0


def check_trace(jaeger: str, trace_id: str, episodes: int, claims: int, capture: bool) -> list[tuple[bool, str]]:
    spans = get_json(f"{jaeger}/api/traces/{trace_id}")["data"][0]["spans"]
    kinds = Counter(s["operationName"].split()[0] for s in spans)
    tags = {t["key"] for s in spans for t in s.get("tags", [])}
    leaked = sorted(tags & CONTENT_ATTRIBUTES)
    return [
        (kinds["run"] == 1, f"one root span 'run podcast-agent' (found {kinds['run']})"),
        (kinds["invoke_agent"] == episodes, f"an invoke_agent span per episode ({kinds['invoke_agent']}/{episodes})"),
        (kinds["verify_claim"] == claims, f"a verify_claim span per fact-check ({kinds['verify_claim']}/{claims})"),
        (kinds["chat"] > 0 and kinds["execute_tool"] > 0,
         f"model and tool spans present ({kinds['chat']} chat, {kinds['execute_tool']} execute_tool)"),
        ("gen_ai.usage.input_tokens" in tags and "gen_ai.request.model" in tags, "GenAI attributes on model spans"),
        (capture or not leaked, "no prompt, completion or query text in spans" + (f" (found {leaked})" if leaked else "")),
    ]


def check_metrics(prom: str, episodes: int, claims: int) -> list[tuple[bool, str]]:
    seen_episodes = prom_value(prom, 'sum(podcast_agent_episodes_total{service_name="podcast-agent"})')
    seen_claims = prom_value(prom, 'sum(podcast_agent_verdicts_total{service_name="podcast-agent"})')
    tokens = prom_value(prom, 'sum(gen_ai_client_token_usage_count{service_name="podcast-agent"})')
    rules = get_json(f"{prom}/api/v1/rules")["data"]["groups"]
    alert_rules = sum(len(g["rules"]) for g in rules)
    return [
        (seen_episodes >= episodes, f"podcast_agent_episodes_total ≥ {episodes} (got {seen_episodes:g})"),
        (seen_claims >= claims, f"podcast_agent_verdicts_total ≥ {claims} (got {seen_claims:g})"),
        (tokens > 0, f"gen_ai_client_token_usage recorded ({tokens:g} model calls)"),
        (alert_rules >= 6, f"alert rules loaded ({alert_rules})"),
    ]


def check_grafana(grafana: str) -> list[tuple[bool, str]]:
    dashboard = get_json(f"{grafana}/api/dashboards/uid/podcast-agent")["dashboard"]
    sources = {d["type"] for d in get_json(f"{grafana}/api/datasources")}
    return [
        (len(dashboard.get("panels", [])) >= 10, f"Grafana dashboard '{dashboard.get('title')}' provisioned"),
        ({"prometheus", "jaeger"} <= sources, f"Grafana data sources provisioned ({', '.join(sorted(sources))})"),
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_dir", type=Path)
    ap.add_argument("--jaeger", default="http://jaeger:16686")
    ap.add_argument("--prometheus", default="http://prometheus:9090")
    ap.add_argument("--grafana", default="http://grafana:3000", help="empty to skip")
    ap.add_argument("--timeout", type=float, default=90.0, help="seconds to wait for the data to arrive")
    ap.add_argument("--capture-content", action="store_true", help="the run had content capture on")
    args = ap.parse_args()

    manifest = json.loads((args.run_dir / "run_manifest.json").read_text())
    report = json.loads((args.run_dir / "report.json").read_text())
    trace_id = manifest.get("otel_trace_id")
    if not trace_id:
        print("FAIL run_manifest.json has no otel_trace_id: was OTEL_EXPORTER_OTLP_ENDPOINT set for the run?")
        return 1
    episodes = len(report["episodes"])
    claims = sum(len(e["fact_checks"]) for e in report["episodes"])
    print(f"run {manifest['run_id']} · trace {trace_id} · {episodes} episodes · {claims} fact-checks")

    deadline = time.monotonic() + args.timeout
    results: list[tuple[bool, str]] = []
    while True:
        try:
            results = check_trace(args.jaeger, trace_id, episodes, claims, args.capture_content)
            results += check_metrics(args.prometheus, episodes, claims)
            if args.grafana:
                results += check_grafana(args.grafana)
        except Exception as exc:  # backend still starting, trace not indexed yet, ...
            results = [(False, f"backends not ready: {exc}")]
        if all(ok for ok, _ in results) or time.monotonic() > deadline:
            break
        time.sleep(5)
    for ok, line in results:
        print(f"{'PASS' if ok else 'FAIL'} {line}")
    print(f"Jaeger: /trace/{trace_id}")
    return 0 if all(ok for ok, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
