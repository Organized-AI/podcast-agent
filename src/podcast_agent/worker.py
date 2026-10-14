"""Queue worker: the production execution mode (`podcast-agent worker`).

    API / uploads ──► queue ──► N stateless workers ──► output store (S3 or a directory)

* Delivery is at-least-once. A job is acknowledged only after its outputs are written; a crash
  or pod eviction leaves the message to be redelivered.
* Jobs are idempotent: the output key is a hash of the transcript bytes + prompt version +
  model ids, so a redelivered job that already finished is acknowledged without new LLM spend.
* Failed jobs are retried up to WORKER_MAX_ATTEMPTS times, then dead-lettered.
* SIGTERM (Kubernetes scale-down / rollout) finishes the current job and stops polling.

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
    def receive(self, wait_s: int) -> Job | None: ...
    def ack(self, job: Job) -> None: ...
    def nack(self, job: Job, error: str) -> None: ...


class OutputStore(Protocol):
    def exists(self, key: str) -> bool: ...
    def put(self, key: str, filename: str, content: str) -> str: ...


# --------------------------------------------------------------------------- local adapters


class DirQueue:
    """Folder-backed queue: atomic rename from inbox/ to processing/ claims a job."""

    def __init__(self, root: str | Path, max_attempts: int = 3):
        self.root = Path(root)
        self.max_attempts = max_attempts
        for name in ("inbox", "processing", "done", "dead-letter"):
            (self.root / name).mkdir(parents=True, exist_ok=True)
        self._attempts = self.root / "attempts.json"

    def _counts(self) -> dict[str, int]:
        return json.loads(self._attempts.read_text()) if self._attempts.exists() else {}

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
                return Job(job_id=path.stem, local_path=target, attempts=attempts)
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
    """Messages look like {"job_id": "...", "transcript_uri": "s3://bucket/key.json"}."""

    def __init__(self, queue_url: str, visibility_timeout_s: int = 900):
        import boto3

        self.url = queue_url
        self.visibility = visibility_timeout_s
        self.sqs = boto3.client("sqs")
        self.s3 = boto3.client("s3")
        self.tmp = Path(tempfile.mkdtemp(prefix="podcast-agent-"))

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
        try:
            key = job_key(job.local_path, ctx)
            if store.exists(key):
                ctx.trace.info("worker", f"{job.job_id}: already processed (key {key}) — acknowledging without rerun")
                queue.ack(job)
                continue
            episode = load_episode(job.local_path)
            report = process_episode(ctx, episode, run_id=f"job-{job.job_id}")
            store.put(key, f"{episode.episode_id}.md", render_markdown(report))
            store.put(key, "report.json", report.model_dump_json(indent=2))  # written last = commit marker
            queue.ack(job)
            processed += 1
            ctx.trace.info("worker", f"{job.job_id}: done → {key}")
        except TranscriptError as exc:  # bad input will never succeed: dead-letter immediately
            job.attempts = 10**6
            queue.nack(job, str(exc))
            ctx.trace.error("worker", f"{job.job_id}: invalid transcript, dead-lettered ({exc})")
        except Exception as exc:  # transient: retry via the queue
            queue.nack(job, str(exc))
            ctx.trace.error("worker", f"{job.job_id}: attempt {job.attempts} failed ({exc}); returned to queue")
    return processed


def copy_into_inbox(queue_root: Path, files: list[Path]) -> None:
    inbox = queue_root / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    for f in files:
        shutil.copy(f, inbox / f.name)
