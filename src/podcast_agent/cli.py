"""Command line entry point: `podcast-agent run | kb | serve`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from podcast_agent import __version__, telemetry
from podcast_agent.config import Settings
from podcast_agent.models import Verdict


def _apply_overrides(settings: Settings, args: argparse.Namespace) -> Settings:
    if getattr(args, "provider", None):
        settings.provider = args.provider
    if getattr(args, "model", None):
        settings.anthropic_model = settings.openai_model = settings.ollama_model = settings.bedrock_model = args.model
    if getattr(args, "fast_model", None):
        settings.anthropic_fast_model = settings.openai_fast_model = settings.bedrock_fast_model = args.fast_model
    if getattr(args, "search", None):
        settings.search_provider = args.search
    if getattr(args, "max_claims", None):
        settings.max_claims = args.max_claims
    if getattr(args, "no_cache", False):
        settings.cache_enabled = False
    if getattr(args, "today", None):
        settings.today = args.today
    return settings


def cmd_run(args: argparse.Namespace) -> int:
    from podcast_agent.runtime import build_context, collect_inputs, new_run_id, run_batch
    from podcast_agent.trace import Tracer

    console = Console(highlight=False)
    settings = _apply_overrides(Settings(), args)
    out_dir = Path(args.out)
    log_dir = out_dir / "logs"
    for name in ("trace.jsonl", "run.log"):
        (log_dir / name).unlink(missing_ok=True)
    run_id = new_run_id()
    tracer = Tracer(run_id, log_dir=log_dir, console=console, verbose=not args.quiet)
    telemetry.setup()
    try:
        files = collect_inputs(args.inputs)
        if not files:
            console.print("[red]No .json or .txt transcripts found.[/red]")
            return 2
        ctx = build_context(settings, tracer)
        if ctx.llm.name == "offline":
            tracer.warn(
                "run",
                "OFFLINE MODE: no model API key found, using deterministic heuristics. Output tests the pipeline "
                "but is not model quality. Set ANTHROPIC_API_KEY (or LLM_PROVIDER=ollama) for real results.",
            )
        reports, errors = run_batch(ctx, files, out_dir, run_id)
    except Exception as exc:  # configuration errors etc.
        console.print(f"[bold red]error:[/bold red] {exc}")
        return 2
    finally:
        tracer.close()
        telemetry.shutdown()

    table = Table(title=f"Run {run_id}", show_lines=False)
    for col in ("Episode", "Summary", "Notes", "Claims ✅/⚠️/❓", "LLM calls", "Tokens in/out"):
        table.add_column(col)
    for r in reports:
        verdicts = [fc.verdict for fc in r.fact_checks]
        approx = "~" if r.usage.estimated else ""
        table.add_row(
            r.episode.episode_id,
            f"{r.summary.word_count}w" if r.summary else "—",
            f"{len(r.notes.takeaways)}T {len(r.notes.quotes)}Q {len(r.notes.topics)}#" if r.notes else "—",
            f"{verdicts.count(Verdict.VERIFIED)}/{verdicts.count(Verdict.OUTDATED)}/{verdicts.count(Verdict.UNVERIFIABLE)}",
            str(r.usage.llm_calls),
            f"{approx}{r.usage.input_tokens:,}/{approx}{r.usage.output_tokens:,}",
        )
    console.print(table)
    console.print(f"Outputs: [bold]{out_dir}/[/bold] (report.md, report.json, <episode>.md/.json, index.md, logs/run.log, logs/trace.jsonl)")
    return 1 if errors else 0


def cmd_audit(args: argparse.Namespace) -> int:
    from podcast_agent.audit import audit_run, write_audit

    console = Console(highlight=False)
    run_dir = Path(args.run_dir)
    if not (run_dir / "run_manifest.json").exists():
        console.print(f"[red]{run_dir} has no run_manifest.json — point audit at a run's output folder.[/red]")
        return 2
    res = audit_run(run_dir, args.transcripts)
    table = Table(title=f"Audit of run {res.run_id}")
    for col in ("Scope", "Area", "Check", "Result", "Detail"):
        table.add_column(col, overflow="fold")
    style = {"pass": "[green]PASS[/green]", "warn": "[yellow]WARN[/yellow]", "fail": "[bold red]FAIL[/bold red]"}
    for ch in res.checks:
        table.add_row(ch.scope, ch.area, ch.name, style[ch.status], ch.detail)
    console.print(table)
    c = res.counts
    console.print(f"{c['pass']} passed · {c['warn']} warnings · {c['fail']} failed")
    if not args.no_write:
        write_audit(res, run_dir)
        console.print(f"Wrote {run_dir}/audit.json and {run_dir}/audit.md")
    return 1 if c["fail"] else 0


def cmd_eval(args: argparse.Namespace) -> int:
    import json

    import yaml

    from podcast_agent.evals.datasets import load_episode_expectations
    from podcast_agent.evals.runner import (
        SuiteResult,
        baseline_snapshot,
        render_markdown,
        run_suite,
        score_run,
        write_reports,
    )
    from podcast_agent.runtime import build_context
    from podcast_agent.trace import Tracer

    console = Console(highlight=False)
    if args.score:  # score run folders only: no suite, no thresholds, no model
        expectations = load_episode_expectations(args.expectations)
        sections = [score_run(d, expectations) for d in args.score]
        result = SuiteResult(suite="score", description="Run folders scored against " + args.expectations,
                             provider="none", sections=sections)
    else:
        suite = yaml.safe_load(Path(args.suite).read_text(encoding="utf-8"))
        ctx = None
        if suite.get("factcheck"):
            settings = _apply_overrides(Settings(), args)
            if suite.get("provider") and not args.provider:
                settings.provider = suite["provider"]
            tracer = Tracer(f"eval-{Path(args.suite).stem}", console=Console(quiet=True), verbose=False)
            ctx = build_context(settings, tracer)
        if ctx is None and any(r.get("fresh") or r.get("judge") for r in suite.get("runs") or []):
            settings = _apply_overrides(Settings(), args)
            if suite.get("provider") and not args.provider:
                settings.provider = suite["provider"]
            ctx = build_context(settings, Tracer(f"eval-{Path(args.suite).stem}", console=Console(quiet=True), verbose=False))
        telemetry.setup()
        try:
            result = run_suite(args.suite, ctx, work_dir=Path(args.out) / "runs" if args.out else None)
        finally:
            telemetry.shutdown()
        if args.update_baseline:
            base = (suite.get("baseline") or {}).get("path")
            if not base:
                console.print("[red]--update-baseline: this suite has no baseline path[/red]")
                return 2
            Path(base).parent.mkdir(parents=True, exist_ok=True)
            Path(base).write_text(json.dumps(baseline_snapshot(result), indent=2) + "\n", encoding="utf-8")
            console.print(f"Wrote baseline {base}")
    print(render_markdown(result))  # raw Markdown, so tables are not re-wrapped
    if args.out:
        json_path, md_path = write_reports(result, args.out)
        console.print(f"Wrote {json_path} and {md_path}")
    misses = [c for c in result.checks if not c.passed]
    for c in misses:
        console.print(f"[bold red]FAIL[/bold red] {c.section}.{c.metric} = {c.value:g} "
                      f"({'needs ≥' if c.direction == 'min' else 'needs ≤'} {c.threshold:g})")
    return 1 if misses else 0


def cmd_kb(args: argparse.Namespace) -> int:
    from podcast_agent.tools.knowledge_base import KnowledgeBase

    settings = Settings()
    kb = KnowledgeBase.load(settings.kb_path)
    console = Console(highlight=False)
    for hit in kb.search(args.query, k=args.k):
        console.print(f"[bold]{hit.entry.id}[/bold] {hit.entry.title}  [dim](bm25 {hit.score}, relevance {hit.relevance}, as of {hit.entry.as_of})[/dim]")
        console.print(f"   {hit.entry.content[:220]}…")
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    import os

    from podcast_agent.runtime import build_context
    from podcast_agent.trace import Tracer
    from podcast_agent.worker import make_queue, make_store, run_worker

    settings = _apply_overrides(Settings(), args)
    tracer = Tracer("worker", console=Console(highlight=False), verbose=not args.quiet)
    ctx = build_context(settings, tracer)
    queue = make_queue(args.queue or os.environ.get("QUEUE_URI", "dir://queue"),
                       max_attempts=int(os.environ.get("WORKER_MAX_ATTEMPTS", "3")))
    store = make_store(args.output or os.environ.get("OUTPUT_URI", "output/jobs"))
    tracer.info("worker", f"polling {args.queue or os.environ.get('QUEUE_URI', 'dir://queue')} · provider={ctx.llm.name}")
    telemetry.setup()
    try:
        n = run_worker(ctx, queue, store, once=args.once)
    finally:
        telemetry.shutdown()
    tracer.info("worker", f"stopped after {n} job(s)")
    tracer.close()
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    telemetry.setup()
    try:
        uvicorn.run("podcast_agent.server:app", host=args.host, port=args.port, log_level="info")
    finally:
        telemetry.shutdown()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="podcast-agent", description="Agentic podcast show notes + fact-checking.")
    parser.add_argument("--version", action="version", version=f"podcast-agent {__version__}")
    sub = parser.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="Process transcript files or directories")
    run.add_argument("inputs", nargs="*", default=["data/transcripts"], help="files or directories (.json/.txt)")
    run.add_argument("--out", default="output", help="output directory (default: output)")
    run.add_argument("--provider", choices=["auto", "anthropic", "bedrock", "openai", "ollama", "offline"])
    run.add_argument("--model", help="main model id (e.g. claude-sonnet-5-5, llama3.1:8b)")
    run.add_argument("--fast-model", help="model used for planning and extraction")
    run.add_argument("--search", choices=["mock", "tavily", "none"], help="web search backend")
    run.add_argument("--max-claims", type=int)
    run.add_argument("--today", help="pin today's date for the fact-checker (YYYY-MM-DD)")
    run.add_argument("--no-cache", action="store_true", help="bypass the local LLM response cache")
    run.add_argument("--quiet", action="store_true", help="hide per-call LLM lines in the console trace")
    run.set_defaults(func=cmd_run)

    audit = sub.add_parser("audit", help="Re-verify a finished run from its files (writes audit.json/audit.md)")
    audit.add_argument("run_dir", nargs="?", default="output", help="the run's output folder (default: output)")
    audit.add_argument("--transcripts", action="append", help="where to find source transcripts if moved")
    audit.add_argument("--no-write", action="store_true", help="print only; don't write audit.json/audit.md")
    audit.set_defaults(func=cmd_audit)

    ev = sub.add_parser("eval", help="Run an eval suite (thresholds gate the exit code) or score run folders")
    ev.add_argument("--suite", default="evals/suites/offline.yaml", help="suite file (default: the offline suite)")
    ev.add_argument("--score", action="append", metavar="RUN_DIR", help="only score these run folders (no model)")
    ev.add_argument("--expectations", default="evals/datasets/episodes.yaml", help="for --score")
    ev.add_argument("--out", help="write eval.json and eval.md here")
    ev.add_argument("--provider", choices=["auto", "anthropic", "bedrock", "openai", "ollama", "offline"])
    ev.add_argument("--model", help="main model id")
    ev.add_argument("--fast-model", help="fast model id")
    ev.add_argument("--no-cache", action="store_true", help="bypass the local LLM response cache")
    ev.add_argument("--update-baseline", action="store_true", help="write this run's gated metrics as the suite's baseline")
    ev.set_defaults(func=cmd_eval)

    kb = sub.add_parser("kb", help="Query the local knowledge base")
    kb.add_argument("query")
    kb.add_argument("-k", type=int, default=3)
    kb.set_defaults(func=cmd_kb)

    worker = sub.add_parser("worker", help="Consume jobs from a queue (dir://path or SQS URL)")
    worker.add_argument("--queue", help="queue URI (default $QUEUE_URI or dir://queue)")
    worker.add_argument("--output", help="output dir or s3://bucket/prefix (default $OUTPUT_URI or output/jobs)")
    worker.add_argument("--provider", choices=["auto", "anthropic", "bedrock", "openai", "ollama", "offline"])
    worker.add_argument("--once", action="store_true", help="drain the queue and exit")
    worker.add_argument("--quiet", action="store_true")
    worker.set_defaults(func=cmd_worker)

    serve = sub.add_parser("serve", help="Run the HTTP API")
    # Containers must listen on all interfaces; outside a container pass --host 127.0.0.1.
    serve.add_argument("--host", default="0.0.0.0")  # noqa: S104
    serve.add_argument("--port", type=int, default=8080)
    serve.set_defaults(func=cmd_serve)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
