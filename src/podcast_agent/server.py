"""HTTP API: the same agent behind FastAPI (`podcast-agent serve`).

The CLI and the API share one image. In production the API only enqueues jobs and stateless
workers run `process_episode` (see docs/DEPLOYMENT.md); this synchronous endpoint is the
local/dev equivalent.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from rich.console import Console

from podcast_agent import __version__
from podcast_agent.agent.context import RunContext
from podcast_agent.agent.pipeline import process_episode
from podcast_agent.config import Settings
from podcast_agent.models import EpisodeReport
from podcast_agent.report import render_markdown
from podcast_agent.runtime import build_context, new_run_id
from podcast_agent.trace import Tracer
from podcast_agent.transcript import TranscriptError, parse_json_transcript, parse_txt_transcript

app = FastAPI(title="Podcast Agent", version=__version__)


class ProcessRequest(BaseModel):
    transcript: dict[str, Any] | list[dict[str, Any]] | None = None  # JSON transcript (take-home format)
    text: str | None = None  # or a timestamped TXT transcript
    episode_id: str = "episode"


class ProcessResponse(BaseModel):
    report: EpisodeReport
    markdown: str


@lru_cache(maxsize=1)
def _shared() -> RunContext:
    # Provider clients, KB index and search client are built once and shared across requests.
    return build_context(Settings(), Tracer("server", console=Console(stderr=True)))


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    ctx = _shared()
    return {"status": "ok", "version": __version__, "provider": ctx.llm.describe(), "kb_entries": len(ctx.kb)}


@app.post("/v1/process", response_model=ProcessResponse)
def process(req: ProcessRequest) -> ProcessResponse:
    try:
        if req.transcript is not None:
            episode = parse_json_transcript(req.transcript, fallback_id=req.episode_id)
        elif req.text:
            episode = parse_txt_transcript(req.text, fallback_id=req.episode_id)
        else:
            raise HTTPException(status_code=422, detail="Provide 'transcript' (JSON) or 'text' (TXT).")
    except TranscriptError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    shared = _shared()
    run_id = new_run_id()
    ctx = RunContext(  # per-request tracer and usage meter; shared clients
        settings=shared.settings, llm=shared.llm, kb=shared.kb, search=shared.search,
        trace=Tracer(run_id, console=Console(stderr=True), verbose=False),
    )
    report = process_episode(ctx, episode, run_id)
    return ProcessResponse(report=report, markdown=render_markdown(report))
