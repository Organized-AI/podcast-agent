import pytest

from podcast_agent.models import timestamp_to_seconds
from podcast_agent.transcript import (
    TranscriptError,
    clean_text,
    load_episode,
    normalize_episode,
    parse_json_transcript,
    parse_txt_transcript,
    resolve_speaker,
)


def test_load_json_resolves_speakers(data_dir):
    ep = load_episode(data_dir / "transcripts" / "ep002_ai_healthcare.json")
    assert ep.episode_id == "ep002"
    assert ep.host == "David Chen"
    priya = next(u for u in ep.utterances if u.speaker == "Dr. Priya")
    assert priya.speaker_name == "Dr. Priya Patel" and priya.role == "guest"
    assert ep.utterances[0].role == "host"
    assert ep.sections[0] == "Introduction"


def test_load_txt_with_header_and_fillers(data_dir):
    ep = load_episode(data_dir / "transcripts" / "ep004_pdf_example.txt")
    assert ep.source_format == "txt"
    assert ep.episode_id == "ep004"
    assert ep.utterances[0].timestamp == "00:02:14"
    assert ep.utterances[0].seconds == 134
    assert ep.utterances[0].speaker_name == "Chris Bell" and ep.utterances[0].role == "host"
    assert ep.utterances[1].speaker_name == "Nina Alvarez" and ep.utterances[1].role == "guest"


def test_txt_continuation_lines_and_unknown_speaker():
    ep = parse_txt_transcript("[00:01] Host: Hello there\nand welcome\n[00:05] Ana: Hi.")
    assert ep.utterances[0].text == "Hello there and welcome"
    assert ep.utterances[1].role == "unknown"


def test_timestamps():
    assert timestamp_to_seconds("01:20") == 80
    assert timestamp_to_seconds("00:02:14") == 134


def test_clean_text_removes_disfluencies_but_keeps_meaning():
    assert clean_text("Um, yeah. The the samples landed, uh, in 2021.")[0] == "Yeah. The samples landed in 2021."
    assert clean_text("Honestly I think, you know, crypto is just, uh, too volatile.")[0] == (
        "Honestly I think crypto is just too volatile."
    )
    assert clean_text("I mean, I heard that a lot.")[0] == "I heard that a lot."
    assert clean_text("That's it, um.")[0] == "That's it."
    # real words that merely contain filler letters are untouched
    assert clean_text("Umbrella sales were hummingbird-level")[0] == "Umbrella sales were hummingbird-level"


def test_normalize_keeps_raw_text(data_dir):
    ep = normalize_episode(load_episode(data_dir / "transcripts" / "ep004_pdf_example.txt"))
    changed = [u for u in ep.utterances if u.text != u.raw_text]
    assert changed and all(u.raw_text for u in changed)
    assert ep.cleaning["fillers"] >= 5


def test_resolve_speaker_generic_labels():
    assert resolve_speaker("Host", "A B", ["C D"]) == ("A B", "host")
    assert resolve_speaker("Guest", "A B", ["C D"]) == ("C D", "guest")
    assert resolve_speaker("Guest", "A B", ["C D", "E F"]) == ("Guest", "guest")


def test_header_pattern_has_no_polynomial_backtracking():
    """CodeQL py/polynomial-redos: `\\s*:\\s*(.+)$` backtracked quadratically on 'host:' followed by many spaces."""
    import time

    from podcast_agent.transcript import HEADER_LINE

    hostile = "host:" + " " * 40_000 + "a\nb"
    started = time.perf_counter()
    assert HEADER_LINE.match(hostile) is None
    assert time.perf_counter() - started < 0.5
    assert HEADER_LINE.match("  Title :  The Future of Remote Work").groups() == ("Title", "The Future of Remote Work")


# Found by fuzz/fuzz_transcript_json.py (#49): malformed shapes raised AttributeError/TypeError (an API 500).
MALFORMED = [
    {"transcript": ["hello"]},
    ["guests"],
    {"transcript": {"a": 1}},
    {"transcript": "not a list"},
    {"transcript": [{"text": "hi"}], "guests": 5},
    {"transcript": [{"text": "hi"}], "guests": ["Ann", 3]},
    {"transcript": [{"text": "hi", "speaker": "A"}], "host": ["x"]},
    {"transcript": [{"text": {"nested": True}}]},
    {"transcript": [{"text": "hi", "section": ["a"]}]},
    {"transcript": [{"text": "hi"}], "title": {"t": 1}},
]


@pytest.mark.parametrize("doc", MALFORMED)
def test_malformed_json_transcripts_raise_transcript_error(doc):
    with pytest.raises(TranscriptError):
        parse_json_transcript(doc)


def test_guests_may_be_a_comma_separated_string():
    ep = parse_json_transcript({"guests": "Dr. Lee, Sam", "transcript": [{"speaker": "Sam", "text": "Hi."}]})
    assert ep.guests == ["Dr. Lee", "Sam"] and ep.utterances[0].role == "guest"


def test_negative_timestamps_do_not_give_negative_seconds():
    ep = parse_json_transcript({"transcript": [{"timestamp": "-5:00", "speaker": "A", "text": "Hi."}]})
    assert ep.utterances[0].seconds == 0

