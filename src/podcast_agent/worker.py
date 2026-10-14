"""Queue worker: the production execution mode (`podcast-agent worker`).

    API / uploads ──► queue ──► N stateless workers ──► output store (S3 or a directory)

* Delivery is at-least-once. A job is acknowledged only after its outputs are written; a crash
  or pod eviction leaves the message to be redelivered.
* Jobs are idempotent: the output key is a hash of the transcript bytes + prompt version +
  model ids, so a redelivered job that already finished is acknowledged without new LLM spend.
* Failed jobs are retried up to WORKER_MAX_ATTEMPTS times, then dead-lettered.
* SIGTERM (Kubernetes scale-down / rollout) finishes the current job and stops polling.
* Jobs carry W3C trace context (``traceparent``), so a job's spans join the trace of the request that
  queued it: API ``POST /v1/jobs`` → ``process <queue>`` (CONSUMER) → ``invoke_agent``.

Queues: `dir://<path>` (inbox/processing/done/dead-letter folders — local dev and tests) or an
SQS queue URL (`https://sqs.<region>.amazonaws.com/...`, needs the `aws` extra). Outputs: a
local directory or `s3://bucket/prefix`. The SQS/S3 adapters are thin boto3 wrappers that are
not exercised in this repo's test suite (no AWS account in CI).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import signal
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from opentelemetry.trace import SpanKind

from podcast_agent import telemetry
from podcast_agent.agent.context import RunContext
from podcast_agent.agent.pipeline import process_episode
from podcast_agent.prompts import PROMPT_VERSION
from podcast_agent.report import render_markdown
from podcast_agent.transcript import TranscriptError, load_episode


@dataclass
class Job:
    job_id: str
    local_path: Path  # transcript materialized on local disk
    attempts: int = 1
    handle: Any = None  # queue-specific receipt
    meta: dict[str, Any] = field(default_factory=dict)


class JobQueue(Protocol):
    system: str  # messaging.system for spans
    name: str  # messaging.destination.name
    def enqueue(self, filename: str, content: bytes, trace_context: dict[str, str] | None = None) -> str: ...
    def receive(self, wait_s: int) -> Job | None: ...
    def ack(self, job: Job) -> None: ...
    def nack(self, job: Job, error: str) -> None: ...


CONTEXT_SUFFIX = ".tracecontext"  # sidecar next to a queued transcript; never mistaken for one


class OutputStore(Protocol):
    def exists(self, key: str) -> bool: ...
    def put(self, key: str, filename: str, content: str) -> str: ...


# --------------------------------------------------------------------------- local adapters


class DirQueue:
    """Folder-backed queue: atomic rename from inbox/ to processing/ claims a job."""

    system = "podcast_agent.dir"

    def __init__(self, root: str | Path, max_attempts: int = 3):
        self.root = Path(root)
        self.name = self.root.name or "queue"
        self.max_attempts = max_attempts
        for name in ("inbox", "processing", "done", "dead-letter"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self._attempts = self.root / "attempts.json"

    def _counts(self) -> dict[str, int]:
        return json.loads(self._attempts.read_text()) if self._attempts.exists() else {}

    def enqueue(self, filename: str, content: bytes, trace_context: dict[str, str] | None = None) -> str:
        """Queue a transcript. The trace-context sidecar is written first and the transcript appears last,
        by rename, so a worker never claims a half-written job."""
        inbox = self.root / "inbox"
        self._write_context(inbox / filename, trace_context)
        staging = self.root / f".{filename}.tmp"
        staging.write_bytes(content)
        staging.rename(inbox / filename)
        return Path(filename).stem

    @staticmethod
    def _write_context(transcript: Path, trace_context: dict[str, str] | None) -> None:
        if trace_context:
            transcript.with_name(transcript.name + CONTEXT_SUFFIX).write_text(json.dumps(trace_context))

    def receive(self, wait_s: int) -> Job | None:
        deadline = time.monotonic() + wait_s
        while True:
            for path in sorted((self.root / "inbox").iterdir()):
                if path.suffix.lower() not in {".json", ".txt"}:
                    continue
                target = self.root / "processing" / path.name
                try:
                    path.rename(target)  # atomic claim; another worker may win the race
                except FileNotFoundError:
                    continue
                attempts = self._counts().get(path.name, 0) + 1
                meta: dict[str, Any] = {}
                sidecar = path.with_name(path.name + CONTEXT_SUFFIX)
                if sidecar.exists():
                    meta["trace_context"] = json.loads(sidecar.read_text())
                    sidecar.unlink()
                return Job(job_id=path.stem, local_path=target, attempts=attempts, meta=meta)
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.5)

    def ack(self, job: Job) -> None:
        job.local_path.rename(self.root / "done" / job.local_path.name)

    def nack(self, job: Job, error: str) -> None:
        counts = self._counts()
        counts[job.local_path.name] = job.attempts
        self._attempts.write_text(json.dumps(counts))
        dest = "dead-letter" if job.attempts >= self.max_attempts else "inbox"
        if dest == "inbox":  # a retry stays in the trace of the request that queued it
            self._write_context(self.root / dest / job.local_path.name, job.meta.get("trace_context"))
        job.local_path.rename(self.root / dest / job.local_path.name)


class DirStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def exists(self, key: str) -> bool:
        return (self.root / key / "report.json").exists()

    def put(self, key: str, filename: str, content: str) -> str:
        path = self.root / key / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return str(path)


# --------------------------------------------------------------------------- AWS adapters


class SQSQueue:  # pragma: no cover - requires AWS
    """Messages look like {"job_id": "...", "transcript_uri": "s3://bucket/key.json", "trace_context": {...}}."""

    system = "aws_sqs"

    def __init__(self, queue_url: str, visibility_timeout_s: int = 900):
        import os

        import boto3

        self.url = queue_url
        self.name = queue_url.rstrip("/").rsplit("/", 1)[-1]
        self.input_uri = os.environ.get("INPUT_URI", "")  # s3://bucket/prefix where enqueue() puts transcripts
        self.visibility = visibility_timeout_s
        self.sqs = boto3.client("sqs")
        self.s3 = boto3.client("s3")
        self.tmp = Path(tempfile.mkdtemp(prefix="podcast-agent-"))

    def enqueue(self, filename: str, content: bytes, trace_context: dict[str, str] | None = None) -> str:
        if not self.input_uri.startswith("s3://"):
            raise ValueError("Set INPUT_URI=s3://bucket/prefix so the API can stage transcripts for SQS jobs")
        bucket, _, prefix = self.input_uri.removeprefix("s3://").partition("/")
        key = "/".join(p for p in (prefix.strip("/"), filename) if p)
        self.s3.put_object(Bucket=bucket, Key=key, Body=content, ServerSideEncryption="aws:kms")
        job_id = Path(filename).stem
        body = {"job_id": job_id, "transcript_uri": f"s3://{bucket}/{key}", "trace_context": trace_context or {}}
        self.sqs.send_message(QueueUrl=self.url, MessageBody=json.dumps(body))
        return job_id

    def receive(self, wait_s: int) -> Job | None:
        resp = self.sqs.receive_message(
            QueueUrl=self.url, MaxNumberOfMessages=1, WaitTimeSeconds=min(wait_s, 20),
            VisibilityTimeout=self.visibility, AttributeNames=["ApproximateReceiveCount"],
        )
        msgs = resp.get("Messages", [])
        if not msgs:
            return None
        msg = msgs[0]
        body = json.loads(msg["Body"])
        bucket, key = body["transcript_uri"].removeprefix("s3://").split("/", 1)
        local = self.tmp / Path(key).name
        self.s3.download_file(bucket, key, str(local))
        return Job(job_id=body.get("job_id") or Path(key).stem, local_path=local,
                   attempts=int(msg["Attributes"].get("ApproximateReceiveCount", 1)),
                   handle=msg["ReceiptHandle"], meta=body)

    def ack(self, job: Job) -> None:
        self.sqs.delete_message(QueueUrl=self.url, ReceiptHandle=job.handle)
        job.local_path.unlink(missing_ok=True)

    def nack(self, job: Job, error: str) -> None:
        # Make it visible again; the queue's redrive policy (maxReceiveCount) moves it to the DLQ.
        self.sqs.change_message_visibility(QueueUrl=self.url, ReceiptHandle=job.handle, VisibilityTimeout=30)
        job.local_path.unlink(missing_ok=True)


class S3Store:  # pragma: no cover - requires AWS
    def __init__(self, uri: str):
        import boto3

        self.bucket, _, self.prefix = uri.removeprefix("s3://").partition("/")
        self.s3 = boto3.client("s3")

    def _key(self, key: str, filename: str) -> str:
        return "/".join(p for p in (self.prefix.strip("/"), key, filename) if p)

    def exists(self, key: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=self._key(key, "report.json"))
            return True
        except Exception:
            return False

    def put(self, key: str, filename: str, content: str) -> str:
        k = self._key(key, filename)
        self.s3.put_object(Bucket=self.bucket, Key=k, Body=content.encode(), ServerSideEncryption="aws:kms")
        return f"s3://{self.bucket}/{k}"


def make_queue(uri: str, max_attempts: int) -> JobQueue:
    if uri.startswith("dir://"):
        return DirQueue(uri.removeprefix("dir://"), max_attempts=max_attempts)
    if uri.startswith("https://sqs."):
        return SQSQueue(uri)
    raise ValueError(f"Unsupported queue URI {uri!r} (use dir://path or an SQS queue URL)")


def make_store(uri: str) -> OutputStore:
    return S3Store(uri) if uri.startswith("s3://") else DirStore(uri)


# --------------------------------------------------------------------------- loop


def job_key(path: Path, ctx: RunContext) -> str:
    h = hashlib.sha256(path.read_bytes())
    h.update(json.dumps({"prompt": PROMPT_VERSION, "models": ctx.llm.describe()}, sort_keys=True).encode())
    return h.hexdigest()[:24]


class _Stop:
    requested = False


def run_worker(ctx: RunContext, queue: JobQueue, store: OutputStore, *, once: bool = False, wait_s: int = 20) -> int:
    """Process jobs until SIGTERM (or until the queue is empty when once=True). Returns jobs processed."""
    stop = _Stop()

    def handle_sigterm(signum, frame):
        stop.requested = True
        ctx.trace.info("worker", "SIGTERM received — finishing the current job, then exiting")

    with contextlib.suppress(ValueError):  # not in the main thread (e.g. tests)
        signal.signal(signal.SIGTERM, handle_sigterm)

    processed = 0
    while not stop.requested:
        job = queue.receive(wait_s=0 if once else wait_s)
        if job is None:
            if once:
                break
            continue
        attributes = {"messaging.system": queue.system, "messaging.operation.type": "process",
                      "messaging.destination.name": queue.name, "messaging.message.id": job.job_id,
                      "podcast_agent.job.attempt": job.attempts}
        with telemetry.attached(job.meta.get("trace_context")), \
                telemetry.span(f"process {queue.name}", kind=SpanKind.CONSUMER, attributes=attributes) as span:
            processed += _process_job(ctx, queue, store, job, span)
    return processed


def _process_job(ctx: RunContext, queue: JobQueue, store: OutputStore, job: Job, span: Any) -> int:
    """Run one job; returns 1 if it produced new outputs. Failures go back to the queue, never up."""
    try:
        key = job_key(job.local_path, ctx)
        if store.exists(key):
            ctx.trace.info("worker", f"{job.job_id}: already processed (key {key}) — acknowledging without rerun")
            span.set_attribute("podcast_agent.job.outcome", "duplicate")
            queue.ack(job)
            return 0
        episode = load_episode(job.local_path)
        report = process_episode(ctx, episode, run_id=f"job-{job.job_id}")
        store.put(key, f"{episode.episode_id}.md", render_markdown(report))
        store.put(key, "report.json", report.model_dump_json(indent=2))  # written last = commit marker
        queue.ack(job)
        span.set_attribute("podcast_agent.job.outcome", "done")
        ctx.trace.info("worker", f"{job.job_id}: done → {key}")
        return 1
    except TranscriptError as exc:  # bad input will never succeed: dead-letter immediately
        telemetry.mark_error(span, exc)
        span.set_attribute("podcast_agent.job.outcome", "dead_lettered")
        job.attempts = 10**6
        queue.nack(job, str(exc))
        ctx.trace.error("worker", f"{job.job_id}: invalid transcript, dead-lettered ({exc})")
    except Exception as exc:  # transient: retry via the queue
        telemetry.mark_error(span, exc)
        span.set_attribute("podcast_agent.job.outcome", "retry")
        queue.nack(job, str(exc))
        ctx.trace.error("worker", f"{job.job_id}: attempt {job.attempts} failed ({exc}); returned to queue")
    return 0


def copy_into_inbox(queue_root: Path, files: list[Path]) -> None:
    inbox = queue_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy(f, inbox / f.name)
