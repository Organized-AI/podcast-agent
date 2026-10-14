import json

from conftest import make_ctx
from rich.console import Console

from podcast_agent.audit import audit_run
from podcast_agent.runtime import run_batch
from podcast_agent.trace import Tracer


def _run(tmp_path, data_dir):
    ctx = make_ctx()
    ctx.trace = Tracer("t", log_dir=tmp_path / "logs", console=Console(quiet=True))
    ctx.trace.run_id = "audit-test"
    files = [data_dir / "transcripts" / "ep001_remote_work.json", data_dir / "transcripts" / "ep004_pdf_example.txt"]
    run_batch(ctx, files, tmp_path, "audit-test")
    ctx.trace.close()
    return tmp_path


def test_manifest_records_versions_settings_and_hashes(tmp_path, data_dir):
    run = _run(tmp_path, data_dir)
    m = json.loads((run / "run_manifest.json").read_text())
    assert m["schema_version"] == "1.1" and m["agent_version"] and m["settings"]["today"] == "2026-10-13"
    assert len(m["knowledge_base"]["sha256"]) == 64
    assert "logs/trace.jsonl" in m["outputs_sha256"] and m["trace_events"] > 50
    assert all(len(e["input"]["sha256"]) == 64 for e in m["episodes"])


def test_audit_passes_on_a_fresh_run(tmp_path, data_dir):
    res = audit_run(_run(tmp_path, data_dir))
    assert res.counts["fail"] == 0, [c for c in res.checks if c.status == "fail"]
    names = {c.name for c in res.checks}
    assert "Every verdict has a complete trace" in names and "Output files match the manifest hashes" in names


def test_audit_catches_tampering(tmp_path, data_dir):
    run = _run(tmp_path, data_dir)
    data = json.loads((run / "report.json").read_text())
    ep = data["episodes"][0]
    ep["notes"]["quotes"][0]["text"] = "Remote work is overrated and nobody should do it."
    ep["fact_checks"][0]["confidence"] = 0.99
    (run / "report.json").write_text(json.dumps(data))
    failed = {c.name for c in audit_run(run).checks if c.status == "fail"}
    assert "Output files match the manifest hashes" in failed
    assert "Quotes verbatim at their timestamp and speaker" in failed
    assert "Confidence within the evidence cap; evidence ids unique" in failed
