"""Transcript ingestion: JSON or timestamped TXT -> Episode, plus disfluency cleanup.

Supported inputs
----------------
* JSON in the take-home format: {episode_id, title, host, guests, transcript: [{timestamp, speaker, section, text}]}
  (a bare list of utterances is also accepted).
* TXT lines such as ``[00:02:14] Host: So NASA recently announced...``. Optional header lines
  (``Title: ...``, ``Host: ...``, ``Guests: a, b``, ``Episode: ...``) may appear before the first
  timestamped line. Untimestamped lines are treated as continuations of the previous utterance.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from podcast_agent.models import Episode, Role, Utterance, timestamp_to_seconds

TXT_LINE = re.compile(r"^\s*\[?(\d{1,2}:\d{2}(?::\d{2})?)\]?\s+([^:\n]{1,40}?):\s*(.*)$")
# The value must start with a non-space so the two runs of spaces cannot overlap (no polynomial backtracking).
HEADER_LINE = re.compile(r"^\s*(title|host|guests?|episode(?:_id)?)\s*:[ \t]*(\S.*)$", re.IGNORECASE)
TITLES = {"dr", "mr", "mrs", "ms", "prof", "sir"}

# Disfluencies that carry no meaning. Discourse words like "so", "well" or "actually"
# are deliberately kept: removing them can change emphasis in a quote.
FILLER_WORDS = re.compile(
    r"(?i)(?:,\s*)?(?<![\w'’-])(?:u+m+|u+h+|e+rm|e+r|a+h+|h+m+|m+h*m+|uh-huh)(?![\w'’-])(?:\s*,)?"
)
FILLER_PHRASES = re.compile(
    r"(?i),\s*(?:you know|i mean|like|sort of|kind of)\s*,|(?:^|(?<=[.!?]\s))(?:you know|i mean|so like|like),"
)
REPEATED_WORD = re.compile(r"(?i)\b([a-z']+)(\s+\1\b)+")
UNSAFE_ID_CHARS = re.compile(r"[^A-Za-z0-9._-]+")
MAX_ID_LENGTH = 64


class TranscriptError(ValueError):
    pass


def safe_episode_id(raw: object, fallback: str = "episode") -> str:
    """Return an episode id that is safe to use as a file name.

    Ids come from transcript content and runs write ``<episode_id>.md`` and ``.json``, so an id such as
    ``../../x`` must not leave the output folder. Characters outside ``[A-Za-z0-9._-]`` become ``-``,
    leading and trailing dots and dashes are dropped (no ``..``, no hidden files) and the id is capped
    at 64 characters. An id with nothing left falls back to ``fallback``, cleaned the same way.
    """
    for candidate in (raw, fallback):
        cleaned = UNSAFE_ID_CHARS.sub("-", str(candidate or "")).strip(".-")[:MAX_ID_LENGTH].rstrip(".-")
        if cleaned:
            return cleaned
    return "episode"


def load_episode(path: str | Path) -> Episode:
    path = Path(path)
    if not path.exists():
        raise TranscriptError(f"Transcript not found: {path}")
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json" or text.lstrip().startswith(("{", "[")):
        return parse_json_transcript(json.loads(text), source_path=str(path), fallback_id=path.stem)
    return parse_txt_transcript(text, source_path=str(path), fallback_id=path.stem)


def parse_json_transcript(data: dict | list, source_path: str | None = None, fallback_id: str = "episode") -> Episode:
    if isinstance(data, list):
        data = {"transcript": data}
    rows = data.get("transcript") or data.get("utterances") or []
    if not rows:
        raise TranscriptError(f"No transcript entries found in {source_path or 'input'}")
    host = data.get("host")
    guests = list(data.get("guests") or [])
    utterances = []
    for i, row in enumerate(rows):
        ts = str(row.get("timestamp") or row.get("time") or "00:00")
        speaker = str(row.get("speaker") or "Unknown").strip()
        name, role = resolve_speaker(speaker, host, guests)
        raw = str(row.get("text") or "").strip()
        utterances.append(
            Utterance(
                idx=i,
                timestamp=ts.strip("[] "),
                seconds=_safe_seconds(ts),
                speaker=speaker,
                speaker_name=name,
                role=role,
                section=row.get("section"),
                text=raw,
                raw_text=raw,
            )
        )
    return Episode(
        episode_id=safe_episode_id(data.get("episode_id"), fallback_id),
        title=str(data.get("title") or fallback_id.replace("_", " ").title()),
        host=host,
        guests=guests,
        utterances=utterances,
        source_path=source_path,
        source_format="json",
    )


def parse_txt_transcript(text: str, source_path: str | None = None, fallback_id: str = "episode") -> Episode:
    header: dict[str, str] = {}
    rows: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        m = TXT_LINE.match(line)
        if m:
            ts, speaker, body = m.groups()
            rows.append({"timestamp": ts, "speaker": speaker.strip(), "text": body.strip()})
            continue
        h = HEADER_LINE.match(line)
        if h and not rows:
            key = h.group(1).lower()
            key = "guests" if key.startswith("guest") else ("episode_id" if key.startswith("episode") else key)
            header[key] = h.group(2).strip()
            continue
        if rows:  # continuation of the previous utterance
            rows[-1]["text"] = f"{rows[-1]['text']} {line.strip()}".strip()
    if not rows:
        raise TranscriptError(f"No timestamped lines found in {source_path or 'input'}")
    data = {
        "episode_id": header.get("episode_id") or fallback_id,
        "title": header.get("title"),
        "host": header.get("host"),
        "guests": [g.strip() for g in header.get("guests", "").split(",") if g.strip()],
        "transcript": rows,
    }
    episode = parse_json_transcript(data, source_path=source_path, fallback_id=fallback_id)
    return episode.model_copy(update={"source_format": "txt"})


def resolve_speaker(label: str, host: str | None, guests: list[str]) -> tuple[str, Role]:
    """Map a transcript label ("Sarah", "Dr. Priya", "Host", "Guest") to a full name and role."""
    low = label.lower().strip()
    if low == "host":
        return (host or "Host"), "host"
    if low == "guest":
        return (guests[0] if len(guests) == 1 else "Guest"), "guest"
    tokens = _name_tokens(label)
    host_role: Role = "host"
    guest_role: Role = "guest"
    candidates: list[tuple[str, Role]] = ([(host, host_role)] if host else []) + [(g, guest_role) for g in guests]
    for name, role in candidates:
        if tokens and tokens <= _name_tokens(name):
            return name, role
    return label, "unknown"


def clean_text(text: str) -> tuple[str, dict[str, int]]:
    """Remove disfluencies; return cleaned text and counts of what was removed."""
    stats = {"fillers": 0, "filler_phrases": 0, "repeated_words": 0}
    stats["fillers"] = len(FILLER_WORDS.findall(text))
    out = FILLER_WORDS.sub(" ", text)
    stats["filler_phrases"] = len(FILLER_PHRASES.findall(out))
    out = FILLER_PHRASES.sub(" ", out)
    stats["repeated_words"] = len(REPEATED_WORD.findall(out))
    out = REPEATED_WORD.sub(r"\1", out)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)  # no space before punctuation
    out = re.sub(r"([,;:])(?=[,.!?;:])", "", out)  # collapse ", ." -> "."
    out = re.sub(r"^[,;:\s]+", "", out)
    out = re.sub(r"(?<=[.!?])\s*[,;:]\s*", " ", out)  # ". , next" -> ". next"
    out = re.sub(r"\s{2,}", " ", out).strip()
    out = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), out)
    return out, stats


def count_fillers(text: str) -> int:
    return len(FILLER_WORDS.findall(text)) + len(FILLER_PHRASES.findall(text)) + len(REPEATED_WORD.findall(text))


def normalize_episode(episode: Episode) -> Episode:
    totals = {"fillers": 0, "filler_phrases": 0, "repeated_words": 0, "utterances_changed": 0}
    new_utts = []
    for u in episode.utterances:
        cleaned, stats = clean_text(u.raw_text)
        for k, v in stats.items():
            totals[k] += v
        if cleaned != u.raw_text:
            totals["utterances_changed"] += 1
        new_utts.append(u.model_copy(update={"text": cleaned or u.raw_text}))
    return episode.model_copy(update={"utterances": new_utts, "cleaning": totals})


def _name_tokens(name: str) -> set[str]:
    return {t for t in re.findall(r"[a-z]+", name.lower()) if t not in TITLES}


def _safe_seconds(ts: str) -> int:
    try:
        return timestamp_to_seconds(ts)
    except ValueError:
        return 0
