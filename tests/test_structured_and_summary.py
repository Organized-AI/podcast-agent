from conftest import ScriptedProvider, make_ctx

from podcast_agent.agent.pipeline import EpisodeState
from podcast_agent.agent.planner import make_plan
from podcast_agent.agent.steps import summarize
from podcast_agent.llm.structured import extract_json
from podcast_agent.transcript import load_episode


def test_extract_json_tolerates_fences_and_prose():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('Sure! Here it is: {"a": {"b": "}"}} hope that helps') == {"a": {"b": "}"}}


def test_summary_self_corrects_when_too_short(data_dir):
    good = " ".join(["word"] * 240)
    provider = ScriptedProvider({"summarize": [{"summary": "Too short."}, {"summary": good}]})
    ctx = make_ctx(provider)
    state = EpisodeState(episode=load_episode(data_dir / "transcripts" / "ep001_remote_work.json"))
    _detail, attempts = summarize(ctx, state, {"strategy": "single_pass"})
    assert attempts == 2
    assert state.summary.word_count == 240
    # the second call carried the validator's feedback back to the model
    retry_messages = provider.calls[1]["messages"]
    assert "must be 200–300 words" in retry_messages[-1]["content"]


def test_summary_accepts_with_warning_after_max_attempts(data_dir):
    provider = ScriptedProvider({"summarize": [{"summary": "short"}] * 3})
    ctx = make_ctx(provider)
    state = EpisodeState(episode=load_episode(data_dir / "transcripts" / "ep001_remote_work.json"))
    summarize(ctx, state, {})
    assert state.summary.text == "short"
    assert any("summary" in w for w in state.warnings)


def test_planner_repairs_a_plan_missing_required_steps(data_dir):
    bad_plan = {"goal": "g", "steps": [{"step": "verify_claims", "reason": "r"}, {"step": "summarize", "reason": "r"}]}
    provider = ScriptedProvider({"plan": [bad_plan, bad_plan, bad_plan]})
    ctx = make_ctx(provider)
    plan = make_plan(ctx, load_episode(data_dir / "transcripts" / "ep003_bootstrapping.json"))
    names = [s.step for s in plan.steps]
    assert {"summarize", "extract_notes", "extract_claims", "verify_claims"} <= set(names)
    assert names.index("extract_claims") < names.index("verify_claims")
    assert plan.repaired


def test_planner_falls_back_when_model_returns_garbage(data_dir):
    provider = ScriptedProvider({"plan": ["not json"] * 3})
    ctx = make_ctx(provider)
    plan = make_plan(ctx, load_episode(data_dir / "transcripts" / "ep003_bootstrapping.json"))
    assert "fallback" in plan.goal
