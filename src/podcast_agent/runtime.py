"""Wiring: build the run context and process a batch of transcript files."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path

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


def run_batch(ctx: RunContext, files: list[Path], out_dir: Path, run_id: str) -> tuple[list[EpisodeReport], list[str]]:
    out_dir.mkdir(parents=True, exist_ok=True)
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
    manifest = {
        "run_id": run_id,
        "prompt_version": PROMPT_VERSION,
        "provider": ctx.llm.describe(),
        "search": getattr(ctx.search, "provider", "none"),
        "knowledge_base_entries": len(ctx.kb),
        "today": ctx.settings.today,
        "episodes": [
            {
                "episode_id": r.episode.episode_id,
                "title": r.episode.title,
                "outputs": [f"{r.episode.episode_id}.json", f"{r.episode.episode_id}.md", "report.md", "report.json"],
                "usage": r.usage.model_dump(),
                "steps": [s.model_dump() for s in r.steps],
            }
            for r in reports
        ],
        "errors": errors,
    }
    (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return reports, errors
