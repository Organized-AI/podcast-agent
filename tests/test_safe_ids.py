"""Episode ids come from transcript content and name output files, so they must not escape the output folder."""

import json

import pytest
from conftest import make_ctx
from rich.console import Console

from podcast_agent.runtime import run_batch
from podcast_agent.trace import Tracer
from podcast_agent.transcript import load_episode, parse_json_transcript, parse_txt_transcript

ROWS = [{"timestamp": "00:00", "speaker": "Host", "text": "Nvidia became the first company to hit a five trillion dollar market cap."}]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("ep001", "ep001"),
        ("ep_002.v2", "ep_002.v2"),
        ("ep 003", "ep-003"),
        ("../../etc/passwd", "etc-passwd"),
        ("..\\..\\windows\\x", "windows-x"),
        ("/abs/path", "abs-path"),
        ("..", "fallback-id"),
        ("", "fallback-id"),
        ("x" * 200, "x" * 64),
    ],
)
def test_json_episode_ids_are_safe_file_names(raw, expected):
    ep = parse_json_transcript({"episode_id": raw, "transcript": ROWS}, fallback_id="fallback-id")
    assert ep.episode_id == expected


def test_txt_header_episode_id_is_safe():
    ep = parse_txt_transcript("Episode: ../../tmp/owned\n[00:00:01] Host: Hello there.\n", fallback_id="t")
    assert ep.episode_id == "tmp-owned"


def test_a_run_cannot_write_outside_its_output_folder(tmp_path):
    src = tmp_path / "in" / "evil.json"
    src.parent.mkdir()
    src.write_text(json.dumps({"episode_id": "../../escaped", "title": "Evil", "transcript": ROWS * 3}))
    out = tmp_path / "work" / "out"
    ctx = make_ctx()
    ctx.trace = Tracer("t", log_dir=out / "logs", console=Console(quiet=True))
    run_batch(ctx, [src], out, "safe-ids")
    ctx.trace.close()
    assert load_episode(src).episode_id == "escaped"
    assert (out / "escaped.md").exists() and (out / "escaped.json").exists()
    written = {p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file()}
    assert all(p.startswith(("in/", "work/out/")) for p in written), written
