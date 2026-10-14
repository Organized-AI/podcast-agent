"""Wiring: build the run context and process a batch of transcript files."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from podcast_agent import __version__, telemetry
from podcast_agent.agent.context import RunContext
from podcast_agent.agent.pipeline import process_episode
from podcast_agent.config import Settings
from podcast_agent.llm import build_provider
from podcast_agent.models import EpisodeReport
from podcast_agent.prompts import PROMPT_VERSION
from podcast_agent.report import render_combined, render_index, render_markdown
from podcast_agent.tools.knowledge_base import KnowledgeBase
from podcast_agent.tools.web_search import MockWebSearch, SearchTool, TavilySearch
from podcast_agent.trace import Tracer
from podcast_agent.transcript import TranscriptError, load_episode

TRANSCRIPT_SUFFIXES = {".json", ".txt"}


def new_run_id() -> str:
    return f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"


def build_search(settings: Settings) -> SearchTool | None:
    if settings.search_provider == "none":
        return None
    if settings.search_provider == "tavily":
        if not settings.tavily_api_key:
            raise ValueError("SEARCH_PROVIDER=tavily requires TAVILY_API_KEY")
        return TavilySearch(settings.tavily_api_key)
    return MockWebSearch(
        settings.mock_web_path,
        latency_ms=settings.mock_search_latency_ms,
        failure_rate=settings.mock_search_failure_rate,
    )


def build_context(settings: Settings, tracer: Tracer) -> RunContext:
    return RunContext(
        settings=settings,
        llm=build_provider(settings),
        trace=tracer,
        kb=KnowledgeBase.load(settings.kb_path),
        search=build_search(settings),
    )


def collect_inputs(inputs: list[str]) -> list[Path]:
    files: list[Path] = []
    for item in inputs:
        path = Path(item)
        if path.is_dir():
            files.extend(sorted(p for p in path.iterdir() if p.suffix.lower() in TRANSCRIPT_SUFFIXES))
        elif path.exists():
            files.append(path)
        else:
            raise FileNotFoundError(f"No such file or directory: {item}")
    return files


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def git_commit() -> str:
    """GIT_COMMIT (set at image build time) or the checkout's HEAD; "unknown" when neither is available."""
    if os.getenv("GIT_COMMIT"):
        return os.environ["GIT_COMMIT"]
    try:
        # `git` from PATH is intended: this only labels the run, and a missing git just means "unknown".
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, timeout=5,  # noqa: S607
                             cwd=Path(__file__).resolve().parent)
        # Only code, config and data make a run irreproducible; regenerated outputs don't count as dirty.
        root = Path(__file__).resolve().parents[2]
        dirty = subprocess.run(["git", "status", "--porcelain", "--untracked-files=no", "--", "src", "pyproject.toml",  # noqa: S607
                                "data"], capture_output=True, text=True, timeout=5, cwd=root)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip() + ("-dirty" if dirty.stdout.strip() else "")
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


def _settings_snapshot(ctx: RunContext) -> dict:
    s = ctx.settings
    return {
        "provider": ctx.llm.name,
        "models": ctx.llm.describe(),
        "search": getattr(ctx.search, "provider", "none"),
        "today": s.today,
        "temperature": s.temperature if ctx.llm.name not in ("anthropic", "bedrock") else None,
        "effort": s.anthropic_effort if ctx.llm.name in ("anthropic", "bedrock") else None,
        "max_claims": s.max_claims,
        "max_steps_per_claim": s.max_steps_per_claim,
        "max_attempts": s.max_attempts,
        "summary_length_mode": s.summary_length_mode,
        "map_reduce_threshold_words": s.map_reduce_threshold_words,
        "prompt_cache": s.prompt_cache,
        "response_cache": s.cache_enabled,
        "mock_search_failure_rate": s.mock_search_failure_rate,
    }


def _rel(path: Path, base: Path) -> str:
    try:
        return str(path.resolve().relative_to(base.resolve()))
    except ValueError:
        return str(path)


def run_batch(ctx: RunContext, files: list[Path], out_dir: Path, run_id: str) -> tuple[list[EpisodeReport], list[str]]:
    """Process every file into ``out_dir`` inside one root span, so a whole run is one trace."""
    attributes = {"podcast_agent.run.id": run_id, "podcast_agent.run.inputs": len(files),
                  "podcast_agent.llm.provider": ctx.llm.name}
    with telemetry.span("run podcast-agent", attributes=attributes) as span:
        reports, errors = _run_batch(ctx, files, out_dir, run_id)
        span.set_attributes({"podcast_agent.run.episodes": len(reports), "podcast_agent.run.errors": len(errors)})
        return reports, errors


def _run_batch(ctx: RunContext, files: list[Path], out_dir: Path, run_id: str) -> tuple[list[EpisodeReport], list[str]]:
    out_dir.mkdir(parents=True, exist_ok=True)
    started_at = datetime.now(UTC).isoformat(timespec="seconds")
    inputs: dict[str, dict] = {}
    reports: list[EpisodeReport] = []
    errors: list[str] = []
    ctx.trace.info(
        "run",
        f"run {run_id} · provider={ctx.llm.name} ({ctx.llm.model}) · kb={len(ctx.kb)} entries · "
        f"search={getattr(ctx.search, 'provider', 'none')} · {len(files)} transcript(s)",
    )
    for path in files:
        try:
            episode = load_episode(path)
        except (TranscriptError, ValueError) as exc:
            ctx.trace.error("ingest", f"{path.name}: {exc}")
            errors.append(f"{path.name}: {exc}")
            continue
        inputs[episode.episode_id] = {"path": str(path), "sha256": sha256_file(path)}
        report = process_episode(ctx, episode, run_id)
        reports.append(report)
        stem = report.episode.episode_id
        (out_dir / f"{stem}.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
        (out_dir / f"{stem}.md").write_text(render_markdown(report), encoding="utf-8")
    ctx.trace.episode_id = None
    (out_dir / "index.md").write_text(render_index(reports, run_id), encoding="utf-8")
    # One combined file per run, as the assignment asks: summary, notes and fact-check table for every episode.
    (out_dir / "report.md").write_text(render_combined(reports, run_id), encoding="utf-8")
    combined = {
        "schema_version": "1.0",
        "run_id": run_id,
        "prompt_version": PROMPT_VERSION,
        "provider": ctx.llm.describe(),
        "today": ctx.settings.today,
        "episodes": [r.model_dump(mode="json") for r in reports],
    }
    (out_dir / "report.json").write_text(json.dumps(combined, indent=2, ensure_ascii=False), encoding="utf-8")
    episode_files = [f"{r.episode.episode_id}.{ext}" for r in reports for ext in ("json", "md")]
    hashed = {name: sha256_file(out_dir / name) for name in ["report.md", "report.json", "index.md", *episode_files]}
    trace_path = ctx.trace.jsonl_path
    if trace_path and trace_path.exists():
        hashed[_rel(trace_path, out_dir)] = sha256_file(trace_path)
    kb_path = Path(ctx.settings.kb_path)
    manifest = {
        "schema_version": "1.2",
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "agent_version": __version__,
        "git_commit": git_commit(),
        "prompt_version": PROMPT_VERSION,
        "provider": ctx.llm.describe(),
        "search": getattr(ctx.search, "provider", "none"),
        "knowledge_base_entries": len(ctx.kb),
        "knowledge_base": {"path": str(kb_path), "entries": len(ctx.kb),
                           "sha256": sha256_file(kb_path) if kb_path.exists() else None},
        "today": ctx.settings.today,
        "settings": _settings_snapshot(ctx),
        "trace_events": ctx.trace.events_written if trace_path else None,
        "otel_trace_id": telemetry.current_ids()[0],
        "outputs_sha256": hashed,
        "episodes": [
            {
                "episode_id": r.episode.episode_id,
                "title": r.episode.title,
                "input": inputs.get(r.episode.episode_id),
                "outputs": [f"{r.episode.episode_id}.json", f"{r.episode.episode_id}.md", "report.md", "report.json"],
                "usage": r.usage.model_dump(),
                "steps": [s.model_dump() for s in r.steps],
                "warnings": r.warnings,
            }
            for r in reports
        ],
        "errors": errors,
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return reports, errors
