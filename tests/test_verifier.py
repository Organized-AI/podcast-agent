from conftest import ScriptedProvider, make_ctx

from podcast_agent.agent.pipeline import process_episode
from podcast_agent.agent.verifier import calibrate, verify_claim
from podcast_agent.models import Claim, Evidence, Verdict
from podcast_agent.tools.web_search import MockWebSearch
from podcast_agent.transcript import load_episode

CLAIM = Claim(id="C1", claim="Bitcoin will reach $200K by 2025.", speaker="Chris Bell", timestamp="00:03:20",
              quote="Bitcoin will reach $200K by 2025.", type="prediction", time_sensitive=True)


def kb(query):
    return {"purpose": "check the KB", "action": "search_knowledge_base", "input": {"query": query}}


def finish(verdict, conf, ids):
    return {"purpose": "done", "action": "finish",
            "input": {"verdict": verdict, "confidence": conf, "rationale": "See [E1].", "evidence_ids": ids}}


def test_react_loop_kb_then_finish_with_calibrated_confidence():
    provider = ScriptedProvider({"verify_step": [kb("bitcoin 2025 high"), finish("possibly_outdated_or_inaccurate", 0.99, ["E1"])]})
    ctx = make_ctx(provider)
    fc = verify_claim(ctx, CLAIM, "ep", max_steps=4, tools=["search_knowledge_base", "web_search"])
    assert fc.verdict == Verdict.OUTDATED
    assert fc.evidence[0].title.startswith("KB-032")
    assert fc.raw_confidence == 0.99 and fc.confidence == 0.9  # single source type caps at 0.90
    assert fc.tool_calls == 1
    # the observation fed back to the model contains the KB entry and its as-of date
    observation = provider.calls[1]["messages"][-1]["content"]
    assert "KB-032" in observation and "as of 2026-10-06" in observation


def test_finish_with_unknown_evidence_id_is_corrected():
    provider = ScriptedProvider({"verify_step": [kb("bitcoin"), finish("verified", 0.8, ["E9"]),
                                                 finish("possibly_outdated_or_inaccurate", 0.8, ["E1"])]})
    ctx = make_ctx(provider)
    fc = verify_claim(ctx, CLAIM, "ep", max_steps=4, tools=["search_knowledge_base"])
    assert fc.verdict == Verdict.OUTDATED
    assert "unknown evidence id E9" in provider.calls[2]["messages"][-1]["content"]


def test_search_outage_is_retried_then_surfaced_to_the_agent():
    provider = ScriptedProvider({"verify_step": [
        {"purpose": "web", "action": "web_search", "input": {"query": "bitcoin"}},
        kb("bitcoin"),
        finish("possibly_outdated_or_inaccurate", 0.7, ["E1"]),
    ]})
    ctx = make_ctx(provider)
    ctx.search = MockWebSearch(ctx.settings.mock_web_path, failure_rate=1.0)
    fc = verify_claim(ctx, CLAIM, "ep", max_steps=4, tools=["search_knowledge_base", "web_search"])
    assert "unavailable" in provider.calls[1]["messages"][-1]["content"]
    assert fc.verdict == Verdict.OUTDATED


def test_budget_exhaustion_defaults_to_unverifiable_for_review():
    provider = ScriptedProvider({"verify_step": [kb("x")] * 20})
    ctx = make_ctx(provider)
    fc = verify_claim(ctx, CLAIM, "ep", max_steps=2, tools=["search_knowledge_base"])
    assert fc.tool_calls == 2
    assert fc.verdict == Verdict.UNVERIFIABLE and fc.confidence == 0.2


def test_calibration_caps():
    kb_ev = Evidence(id="E1", source="knowledge_base", title="t", snippet="s")
    web_ev = Evidence(id="E2", source="web_search", title="t", snippet="s")
    assert calibrate(Verdict.UNVERIFIABLE, 0.9, []) == 0.5
    assert calibrate(Verdict.VERIFIED, 0.9, []) == 0.6
    assert calibrate(Verdict.VERIFIED, 0.99, [kb_ev]) == 0.9
    assert calibrate(Verdict.VERIFIED, 0.99, [kb_ev, web_ev]) == 0.95
    assert calibrate(Verdict.VERIFIED, 0.7, [kb_ev, web_ev]) == 0.7


def test_offline_end_to_end_on_the_pdf_example(data_dir):
    ctx = make_ctx()
    report = process_episode(ctx, load_episode(data_dir / "transcripts" / "ep004_pdf_example.txt"), "test")
    verdicts = {fc.claim.quote: fc.verdict for fc in report.fact_checks}
    def find(fragment):
        return next(v for q, v in verdicts.items() if fragment in q)
    assert find("early 2026") == Verdict.OUTDATED          # Mars Sample Return was delayed/restructured
    assert find("Perseverance") == Verdict.VERIFIED       # landed Feb 2021
    assert find("$200K") == Verdict.OUTDATED              # peaked ~$126K in 2025
    assert find("before 2040") == Verdict.UNVERIFIABLE     # future prediction
    assert all(s.status == "ok" for s in report.steps)
    assert report.summary and report.notes and len(report.notes.takeaways) == 5


def test_a_refusal_is_not_retried_and_the_claim_goes_to_human_review():
    from podcast_agent.llm.base import LLMRefusal

    provider = ScriptedProvider({"verify_step": [LLMRefusal("declined (reasoning_extraction): ...", "reasoning_extraction")]})
    ctx = make_ctx(provider)
    fc = verify_claim(ctx, CLAIM, "ep", max_steps=4, tools=["search_knowledge_base"])
    assert fc.verdict == Verdict.UNVERIFIABLE and fc.tool_calls == 0
    assert len(provider.calls) == 1  # declined once, never resent


def test_the_turn_format_asks_for_a_purpose_not_private_reasoning():
    from podcast_agent.prompts import verify_system

    system = verify_system("2026-10-13", 4, ["search_knowledge_base", "web_search"]).lower()
    assert '"purpose"' in system
    for banned in ('"thought"', '"reasoning"', '"thinking"', "scratchpad", "your reasoning"):
        assert banned not in system


def test_near_miss_turns_are_accepted_and_real_errors_are_named():
    provider = ScriptedProvider({"verify_step": [
        {"purpose": "kb", "action": "search_knowledge_base", "input": "bitcoin 2025 high"},  # query as a plain string
        {"purpose": "oops", "action": "search_everything", "input": {"query": "x"}},  # unknown action
        {"purpose": "done", "action": "finish", "verdict": "possibly_outdated_or_inaccurate",  # fields beside input
         "confidence": 0.8, "reason": "See [E1].", "evidence": ["E1"]},
    ]})
    ctx = make_ctx(provider)
    fc = verify_claim(ctx, CLAIM, "ep", max_steps=4, tools=["search_knowledge_base"])
    assert fc.tool_calls == 1 and fc.verdict == Verdict.OUTDATED and fc.evidence[0].id == "E1"
    correction = provider.calls[2]["messages"][-1]["content"]
    assert correction.startswith("That reply could not be used (action:")


MARS = Claim(id="C5", claim="Humans will walk on Mars before 2040.", speaker="Nina Alvarez", timestamp="00:04:06",
             quote="I think humans will walk on Mars before 2040.", type="prediction", time_sensitive=True)


def test_an_unexpired_prediction_is_unverifiable_whatever_the_model_says():
    """#13: samples/claude-run has this claim as ⚠️ 0.60; the deadline is 2040, so it cannot be checked yet."""
    provider = ScriptedProvider({"verify_step": [kb("crewed Mars landing"), finish("possibly_outdated_or_inaccurate", 0.6, ["E1"])]})
    ctx = make_ctx(provider)
    fc = verify_claim(ctx, MARS, "ep", max_steps=4, tools=["search_knowledge_base"])
    assert fc.verdict == Verdict.UNVERIFIABLE
    assert fc.confidence <= 0.5 and fc.raw_confidence == 0.6
    assert "deadline has not passed" in fc.rationale


def test_a_prediction_whose_deadline_passed_keeps_the_models_verdict():
    provider = ScriptedProvider({"verify_step": [kb("bitcoin 2025 high"), finish("possibly_outdated_or_inaccurate", 0.8, ["E1"])]})
    fc = verify_claim(make_ctx(provider), CLAIM, "ep", max_steps=4, tools=["search_knowledge_base"])
    assert fc.verdict == Verdict.OUTDATED


def test_deadline_status():
    from podcast_agent.agent.policy import deadline_status, prediction_is_checkable

    today = "2026-10-13"
    assert deadline_status("Bitcoin will reach $200K by 2025.", today) == "passed"
    assert deadline_status("NASA launches in early 2026.", today) == "passed"
    assert deadline_status("A launch in late 2026.", today) == "pending"
    assert deadline_status("Humans will walk on Mars before 2040.", today) == "pending"
    assert deadline_status("Async culture will become standard.", today) == "none"
    assert prediction_is_checkable("factual", "Something in 2040.", today)
    assert not prediction_is_checkable("prediction", "Async culture will become standard.", today)
