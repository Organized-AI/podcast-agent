"""HTTP API: the same agent behind FastAPI (`podcast-agent serve`).

The CLI and the API share one image. In production the API only enqueues jobs (`POST /v1/jobs`) and
stateless workers run `process_episode` (see docs/DEPLOYMENT.md); `POST /v1/process` is the synchronous
local/dev equivalent. Every request runs in an OpenTelemetry SERVER span that continues an incoming
`traceparent`, and queued jobs carry the request's trace context to the worker.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import MutableMapping
from functools import lru_cache
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from rich.console import Console

from podcast_agent import __version__, telemetry
from podcast_agent.agent.context import RunContext
from podcast_agent.agent.pipeline import process_episode
from podcast_agent.config import Settings
from podcast_agent.models import Episode, EpisodeReport
from podcast_agent.report import render_markdown
from podcast_agent.runtime import build_context, git_commit, new_run_id
from podcast_agent.trace import Tracer
from podcast_agent.transcript import TranscriptError, parse_json_transcript, parse_txt_transcript
from podcast_agent.worker import make_queue


def _probe(scope: MutableMapping[str, Any]) -> bool:
    """Liveness and readiness probes would be most of the traffic; keep them out of telemetry."""
    return scope.get("path") == "/healthz"


# FastAPI's native OpenTelemetry support makes a SERVER span per request (continuing an incoming
# `traceparent`) and records HTTP server metrics through the providers `telemetry.setup()` installs.
app = FastAPI(title="Podcast Agent", version=__version__, telemetry={"exclude": _probe})


class ProcessRequest(BaseModel):
    transcript: dict[str, Any] | list[dict[str, Any]] | None = None  # JSON transcript (take-home format)
    text: str | None = None  # or a timestamped TXT transcript
    episode_id: str = "episode"


class ProcessResponse(BaseModel):
    report: EpisodeReport
    markdown: str


class JobResponse(BaseModel):
    job_id: str
    queue: str
    trace_id: str | None = None


@lru_cache(maxsize=1)
def _shared() -> RunContext:
    # Provider clients, KB index and search client are built once and shared across requests.
    return build_context(Settings(), Tracer("server", console=Console(stderr=True)))


@lru_cache(maxsize=1)
def _commit() -> str:
    # The image's GIT_COMMIT (sha-<commit> from the publish workflow), read once: a rollout shows up here.
    return git_commit()


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    ctx = _shared()
    return {"status": "ok", "version": __version__, "commit": _commit(), "provider": ctx.llm.describe(),
            "kb_entries": len(ctx.kb)}


def _parse(req: ProcessRequest) -> Episode:
    try:
        if req.transcript is not None:
            return parse_json_transcript(req.transcript, fallback_id=req.episode_id)
        if req.text:
            return parse_txt_transcript(req.text, fallback_id=req.episode_id)
    except TranscriptError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    raise HTTPException(status_code=422, detail="Provide 'transcript' (JSON) or 'text' (TXT).")


@app.post("/v1/jobs", response_model=JobResponse, status_code=202)
def enqueue(req: ProcessRequest) -> JobResponse:
    """Validate a transcript and queue it for the workers; the job carries this request's trace context."""
    queue_uri = os.environ.get("QUEUE_URI")
    if not queue_uri:
        raise HTTPException(status_code=503, detail="This deployment has no job queue (set QUEUE_URI).")
    episode = _parse(req)  # reject bad input now, not after it reaches a worker
    if req.transcript is not None:
        filename, content = f"{uuid.uuid4().hex[:12]}.json", json.dumps(req.transcript).encode()
    else:
        filename, content = f"{uuid.uuid4().hex[:12]}.txt", (req.text or "").encode()
    queue = make_queue(queue_uri, max_attempts=int(os.environ.get("WORKER_MAX_ATTEMPTS", "3")))
    job_id = queue.enqueue(filename, content, telemetry.inject())
    telemetry.add_event("info", "enqueue", f"queued {episode.episode_id} as job {job_id}")
    return JobResponse(job_id=job_id, queue=queue.name, trace_id=telemetry.current_ids()[0])


@app.post("/v1/process", response_model=ProcessResponse)
def process(req: ProcessRequest) -> ProcessResponse:
    episode = _parse(req)
    shared = _shared()
    run_id = new_run_id()
    ctx = RunContext(  # per-request tracer and usage meter; shared clients
        settings=shared.settings, llm=shared.llm, kb=shared.kb, search=shared.search,
        trace=Tracer(run_id, console=Console(stderr=True), verbose=False),
    )
    report = process_episode(ctx, episode, run_id)
    return ProcessResponse(report=report, markdown=render_markdown(report))
