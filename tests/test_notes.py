from conftest import ScriptedProvider, make_ctx

from podcast_agent.agent.pipeline import EpisodeState
from podcast_agent.agent.steps import extract_notes, locate_span
from podcast_agent.transcript import load_episode

TAKEAWAYS = [{"text": f"Takeaway {i}", "timestamp": "00:45"} for i in range(5)]
TOPICS = ["Remote Work", "async-communication", "documentation", "company culture", "#trust"]


def _state(data_dir):
    return EpisodeState(episode=load_episode(data_dir / "transcripts" / "ep001_remote_work.json"))


def test_locate_span_finds_line_even_with_wrong_timestamp(data_dir):
    ep = load_episode(data_dir / "transcripts" / "ep001_remote_work.json")
    u, score = locate_span(ep, "Remote doesn’t mean isolated; it just needs intention.", "00:00")
    assert u.timestamp == "04:10" and score == 1.0


def test_paraphrased_quote_is_rejected_then_fixed(data_dir):
    bad = {"takeaways": TAKEAWAYS, "topics": TOPICS, "quotes": [
        {"text": "Remote work is about trust and processes, not location.", "speaker": "Mark Rivera", "timestamp": "00:45"},
        {"text": "Outcomes over hours.", "speaker": "Mark Rivera", "timestamp": "03:25"},
        {"text": "Remote doesn’t mean isolated; it just needs intention.", "speaker": "Mark Rivera", "timestamp": "04:10"},
    ]}
    good = {"takeaways": TAKEAWAYS, "topics": TOPICS, "quotes": [
        {"text": "Being remote doesn’t just mean a location; it means structured processes and trust.",
         "speaker": "Mark", "timestamp": "00:15"},
        {"text": "Transparency matters more than check-ins.", "speaker": "Mark Rivera", "timestamp": "03:25"},
        {"text": "Remote doesn’t mean isolated; it just needs intention.", "speaker": "Mark Rivera", "timestamp": "04:10"},
    ]}
    provider = ScriptedProvider({"extract_notes": [bad, good]})
    ctx, state = make_ctx(provider), _state(data_dir)
    _detail, attempts = extract_notes(ctx, state, {})
    assert attempts == 2
    assert "not verbatim" in provider.calls[1]["messages"][-1]["content"]
    q0 = state.notes.quotes[0]
    # wrong timestamp/speaker in the model output were repaired from the transcript
    assert q0.timestamp == "00:45" and q0.speaker == "Mark Rivera"
    assert state.notes.topics[0] == "remote-work" and "trust" in state.notes.topics


def test_offline_notes_meet_the_contract(data_dir):
    ctx, state = make_ctx(), _state(data_dir)
    extract_notes(ctx, state, {})
    n = state.notes
    assert len(n.takeaways) == 5
    assert 3 <= len(n.quotes) <= 5 and all(q.match_score >= 0.9 for q in n.quotes)
    assert 4 <= len(n.topics) <= 8
