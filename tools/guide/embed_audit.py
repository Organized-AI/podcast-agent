#!/usr/bin/env python3
"""Embed a real run's audit trail into the guide's Audit trail tab.

    python tools/guide/embed_audit.py samples/claude-run samples/proof website/public/index.html   (or: make guide)

Reads the run manifest, report.json, logs/trace.jsonl and audit.json from a run folder, plus the
captured build/test outputs in the proof folder, and writes them as one JSON block into the page
between <script type="application/json" id="audit-data"> and </script>. It also copies the run's
files (and the transcripts and knowledge base it read) next to the page under run/, so a reader can
open them and re-hash them in the browser against the manifest. Nothing is retyped.

Run `podcast-agent audit <run>` first so audit.json exists. Run from the repository root.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

ANSI = re.compile(r"\x1b\[[0-9;]*m")
MAX_DATA_CHARS = 1800
MAX_OUTPUT_LINES = 140

# file, title, kind, headline pattern, expected outcome ("pass", or "fail" for the tamper test)
PROOF = [
    ("pytest.txt", "Test suite", "tests", r"=+ (\d+ passed[^=]*?) in", "pass"),
    ("mac-pytest.txt", "Test suite on the MacBook (Python 3.12 container)", "tests", r"=+ (\d+ passed[^=]*?) in", "pass"),
    ("ruff.txt", "Lint", "lint", r"(All checks passed!)", "pass"),
    ("helm.txt", "Helm chart: lint and Kubernetes schema check", "deploy",
     r"(Valid: \d+, Invalid: \d+, Errors: \d+, Skipped: \d+)", "pass"),
    ("mac-docker-build.txt", "Docker build on the MacBook", "docker", r"os/arch (\S+)", "pass"),
    ("mac-docker-run-claude.txt", "Claude run on the MacBook (Sonnet 5.5 + Haiku 5.5), all four episodes", "docker",
     r"(exit: \d+)", "pass"),
    ("mac-docker-audit-claude.txt", "Audit of the Claude run, inside the container", "audit",
     r"(\d+ passed · \d+ warnings? · \d+ failed)", "pass"),
    ("mac-docker-run.txt", "Offline run on the MacBook (no API key), all four episodes", "docker", r"(exit: \d+)", "pass"),
    ("mac-docker-audit.txt", "Audit of the offline MacBook run, inside the container", "audit",
     r"(\d+ passed · \d+ warnings? · \d+ failed)", "pass"),
    ("audit-workspace.txt", "Audit of the committed offline sample", "audit",
     r"(\d+ passed · \d+ warnings? · \d+ failed)", "pass"),
    ("audit-tampered.txt", "Tamper test: the audit catches edited output", "audit",
     r"(\d+ passed · \d+ warnings? · \d+ failed)", "fail"),
]
FAILED = re.compile(r"\b[1-9]\d* failed|Invalid: [1-9]|exit: [1-9]|\bERROR\b")


def read_header(text: str) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    meta: dict[str, str] = {}
    start = 0
    for i, line in enumerate(lines):
        m = re.match(r"^# (command|machine|commit|captured): (.*)$", line)
        if not m:
            start = i
            break
        meta[m.group(1)] = m.group(2)
    return meta, "\n".join(lines[start:]).strip("\n")


def proof_items(proof_dir: Path) -> list[dict]:
    items = []
    for fname, title, kind, pattern, expect in PROOF:
        path = proof_dir / fname
        if not path.exists():
            continue
        meta, body = read_header(ANSI.sub("", path.read_text(encoding="utf-8")))
        m = re.search(pattern, body)
        headline = m.group(1) if m else ""
        if fname == "mac-docker-build.txt":
            commit = re.search(r"GIT_COMMIT=(\S+)", body)
            headline = f"built · {headline}" + (f" · GIT_COMMIT={commit.group(1)}" if commit else "")
        outcome = "fail" if FAILED.search(body) else "pass"
        status = "caught" if expect == "fail" and outcome == "fail" else outcome if expect == "pass" else "fail"
        lines = body.splitlines()
        if len(lines) > MAX_OUTPUT_LINES:
            lines = [*lines[:MAX_OUTPUT_LINES], f"… {len(lines) - MAX_OUTPUT_LINES} more lines in samples/proof/{fname}"]
        items.append({"id": fname.removesuffix(".txt"), "file": f"samples/proof/{fname}", "title": title, "kind": kind,
                      "status": status, "headline": headline, **meta, "output": "\n".join(lines)})
    return items


def publish_files(run: Path, manifest: dict, dest: Path) -> list[dict]:
    """Copy the run's outputs and inputs next to the page; return what the browser can re-hash."""
    if dest.exists():
        shutil.rmtree(dest)
    files = []
    for rel, sha in manifest["outputs_sha256"].items():
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(run / rel, target)
        files.append({"group": "output", "path": rel, "url": f"run/{rel}", "sha256": sha})
    for extra in ("run_manifest.json", "audit.json", "audit.md", "logs/run.log"):
        if (run / extra).exists():
            (dest / extra).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(run / extra, dest / extra)
    inputs = [(e["episode_id"], e["input"]["path"], e["input"]["sha256"]) for e in manifest["episodes"]]
    kb = manifest["knowledge_base"]
    inputs.append(("knowledge base", kb["path"], kb["sha256"]))
    for label, src, sha in inputs:
        name = Path(src).name
        (dest / "inputs").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(src), dest / "inputs" / name)
        files.append({"group": "input", "label": label, "path": src, "url": f"run/inputs/{name}", "sha256": sha})
    return files


def main(run_dir: str, proof_dir: str, html_path: str) -> None:
    run, proof, html = Path(run_dir), Path(proof_dir), Path(html_path)
    manifest = json.loads((run / "run_manifest.json").read_text(encoding="utf-8"))
    combined = json.loads((run / "report.json").read_text(encoding="utf-8"))
    audit = json.loads((run / "audit.json").read_text(encoding="utf-8"))

    episodes, claims = [], []
    for ep in combined["episodes"]:
        meta = ep["episode"]
        plan = ep.get("plan") or {}
        episodes.append({
            "id": meta["episode_id"], "title": meta["title"], "words": meta["word_count"],
            "speakers": [meta.get("host"), *list(meta.get("guests") or [])],
            "summary_words": (ep.get("summary") or {}).get("word_count"),
            "summary_strategy": (ep.get("summary") or {}).get("strategy"),
            "plan": [{"step": s["step"], "params": s.get("params") or {}, "reason": s.get("reason", "")}
                     for s in plan.get("steps", [])],
            "plan_repairs": plan.get("repair_notes", []),
            "steps": ep["steps"], "usage": ep["usage"], "warnings": ep["warnings"],
        })
        for fc in ep["fact_checks"]:
            c = fc["claim"]
            claims.append({
                "ep": meta["episode_id"], "id": c["id"], "claim": c["claim"], "speaker": c["speaker"],
                "ts": c["timestamp"], "type": c["type"], "time_sensitive": c["time_sensitive"],
                "verdict": fc["verdict"], "verification": fc["verification"], "method": fc["method"],
                "conf": fc["confidence"], "raw": fc["raw_confidence"], "tool_calls": fc["tool_calls"],
                "tools": fc["tools_used"], "rationale": fc["rationale"],
                "evidence": [{"id": e["id"], "source": e["source"], "title": e["title"], "date": e.get("date")}
                             for e in fc["evidence"]],
            })

    trace = []
    for line in (run / "logs" / "trace.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        data = e.get("data")
        data_s = json.dumps(data, ensure_ascii=False, indent=1) if data else ""
        if len(data_s) > MAX_DATA_CHARS:
            data_s = data_s[:MAX_DATA_CHARS] + "\n… (trimmed for the page; the full event is in logs/trace.jsonl)"
        trace.append({"ts": e["ts"], "ep": e.get("episode_id") or "run", "step": e.get("step") or "",
                      "kind": e["kind"], "msg": e["message"], "data": data_s})

    mac = {}
    if (proof / "mac-run_manifest.json").exists():
        mm = json.loads((proof / "mac-run_manifest.json").read_text(encoding="utf-8"))
        ma = json.loads((proof / "mac-audit.json").read_text(encoding="utf-8")) if (proof / "mac-audit.json").exists() else {}
        mac = {"run_id": mm["run_id"], "git_commit": mm.get("git_commit"), "trace_events": mm.get("trace_events"),
               "audit": ma.get("summary", {})}

    payload = {
        "embedded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "run_dir": str(run),
        "run": {k: manifest.get(k) for k in ("run_id", "started_at", "finished_at", "agent_version", "git_commit",
                                              "prompt_version", "provider", "search", "today", "settings",
                                              "knowledge_base", "trace_events")},
        "episodes": episodes, "claims": claims, "audit": audit, "trace": trace,
        "proof": proof_items(proof), "mac_run": mac,
        "files": publish_files(run, manifest, html.parent / "run"),
    }
    blob = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    page = html.read_text(encoding="utf-8")
    pattern = re.compile(r'(<script type="application/json" id="audit-data">)(.*?)(</script>)', re.S)
    if not pattern.search(page):
        raise SystemExit(f'{html} has no <script type="application/json" id="audit-data"> block')
    page = pattern.sub(lambda m: m.group(1) + blob + m.group(3), page, count=1)
    html.write_text(page, encoding="utf-8")
    print(f"embedded run {manifest['run_id']} · {len(trace)} trace events · {len(claims)} claims · "
          f"{len(audit['checks'])} checks · {len(payload['proof'])} proof items · {len(payload['files'])} files "
          f"→ {html} ({len(blob):,} bytes of data)")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    main(*sys.argv[1:])
