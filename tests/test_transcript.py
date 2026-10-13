from podcast_agent.models import timestamp_to_seconds
from podcast_agent.transcript import (
    clean_text,
    load_episode,
    normalize_episode,
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
