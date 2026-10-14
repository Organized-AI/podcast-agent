"""Trace context crosses the HTTP API and the job queue, so one trace covers request → queue → worker."""

from __future__ import annotations

import json

import pytest
from conftest import make_ctx
from fastapi.testclient import TestClient
from opentelemetry.trace import SpanKind

from podcast_agent.worker import CONTEXT_SUFFIX, DirQueue, DirStore, run_worker

REMOTE_TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"
REMOTE_PARENT = "00f067aa0ba902b7"
TRACEPARENT = f"00-{REMOTE_TRACE}-{REMOTE_PARENT}-01"


@pytest.fixture
def client(monkeypatch, data_dir):
    monkeypatch.setenv("LLM_PROVIDER", "offline")
    monkeypatch.setenv("KB_PATH", str(data_dir / "knowledge_base.json"))
    monkeypatch.setenv("MOCK_WEB_PATH", str(data_dir / "mock_web_index.json"))
    from podcast_agent import server

    server._shared.cache_clear()
    return TestClient(server.app)


def _is_descendant(span, ancestor, spans) -> bool:
    by_id = {s.context.span_id: s for s in spans}
    while span.parent is not None:
        if span.parent.span_id == ancestor.context.span_id:
            return True
        span = by_id.get(span.parent.span_id)
        if span is None:
            return False
    return False


def _payload(data_dir, name="ep003_bootstrapping.json"):
    return {"transcript": json.loads((data_dir / "transcripts" / name).read_text())}


def test_a_request_continues_the_callers_trace(otel, client, data_dir):
    exporter, _ = otel
    exporter.clear()
    r = client.post("/v1/process", json=_payload(data_dir), headers={"traceparent": TRACEPARENT})
    assert r.status_code == 200
    finished = list(exporter.get_finished_spans())
    spans = {s.name: s for s in finished}
    srv = spans["POST /v1/process"]  # FastAPI's native OpenTelemetry SERVER span
    assert srv.kind == SpanKind.SERVER and srv.attributes["http.route"] == "/v1/process"
    assert srv.attributes["http.response.status_code"] == 200
    assert format(srv.context.trace_id, "032x") == REMOTE_TRACE
    assert srv.parent is not None and format(srv.parent.span_id, "016x") == REMOTE_PARENT
    assert _is_descendant(spans["invoke_agent podcast-agent"], srv, finished)


def test_a_queued_job_runs_in_the_trace_of_the_request_that_queued_it(otel, client, data_dir, tmp_path, monkeypatch):
    exporter, _ = otel
    monkeypatch.setenv("QUEUE_URI", f"dir://{tmp_path / 'q'}")
    exporter.clear()
    r = client.post("/v1/jobs", json=_payload(data_dir), headers={"traceparent": TRACEPARENT})
    assert r.status_code == 202
    job = r.json()
    assert job["trace_id"] == REMOTE_TRACE and job["queue"] == "q"
    inbox = tmp_path / "q" / "inbox"
    assert (inbox / f"{job['job_id']}.json").exists()
    assert json.loads((inbox / f"{job['job_id']}.json{CONTEXT_SUFFIX}").read_text())["traceparent"].startswith(f"00-{REMOTE_TRACE}-")

    queue = DirQueue(tmp_path / "q")
    assert run_worker(make_ctx(), queue, DirStore(tmp_path / "out"), once=True) == 1
    spans = list(exporter.get_finished_spans())
    srv = next(s for s in spans if s.name == "POST /v1/jobs")
    consumer = next(s for s in spans if s.name == "process q")
    agent = next(s for s in spans if s.name == "invoke_agent podcast-agent")
    assert consumer.kind == SpanKind.CONSUMER and consumer.attributes["messaging.message.id"] == job["job_id"]
    assert consumer.attributes["podcast_agent.job.outcome"] == "done"
    assert {s.context.trace_id for s in (srv, consumer, agent)} == {srv.context.trace_id}
    assert _is_descendant(consumer, srv, spans)
    assert agent.parent is not None and agent.parent.span_id == consumer.context.span_id
    assert not list(inbox.iterdir())  # sidecar consumed with the job


def test_a_retried_job_keeps_its_trace_context(tmp_path):
    q = DirQueue(tmp_path / "q", max_attempts=3)
    carrier = {"traceparent": TRACEPARENT}
    q.enqueue("job1.txt", b"[00:00:01] Host: Hello.", carrier)
    job = q.receive(wait_s=0)
    assert job is not None and job.meta["trace_context"] == carrier
    q.nack(job, "transient")
    again = q.receive(wait_s=0)
    assert again is not None and again.attempts == 2 and again.meta["trace_context"] == carrier


def test_jobs_endpoint_needs_a_queue_and_valid_input(client, data_dir, tmp_path, monkeypatch):
    assert client.post("/v1/jobs", json=_payload(data_dir)).status_code == 503
    monkeypatch.setenv("QUEUE_URI", f"dir://{tmp_path / 'q'}")
    assert client.post("/v1/jobs", json={"text": "no timestamps here"}).status_code == 422
    assert not (tmp_path / "q" / "inbox").exists() or not list((tmp_path / "q" / "inbox").iterdir())
    r = client.post("/v1/jobs", json={"text": "[00:00:01] Host: Nvidia hit five trillion dollars.\n"})
    assert r.status_code == 202 and (tmp_path / "q" / "inbox" / f"{r.json()['job_id']}.txt").exists()


def test_health_probes_stay_out_of_telemetry(otel, client):
    exporter, _ = otel
    exporter.clear()
    assert client.get("/healthz").status_code == 200
    assert not [s for s in exporter.get_finished_spans() if "healthz" in s.name]
