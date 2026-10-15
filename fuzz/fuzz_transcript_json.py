"""Fuzz the JSON transcript parser with arbitrary JSON documents.

The API accepts a transcript as JSON, so any object or array must either parse into an episode with a
file-safe id or raise TranscriptError (the API's 422). Any other exception would be a 500.

Half of the inputs are raw JSON text; the other half are documents built from the fuzzer's bytes in the
shape of a transcript, with any JSON type in any field, which reaches the parser's branches far faster
than mutated text that rarely stays valid JSON.
"""

import json
import sys

import atheris

with atheris.instrument_imports():
    from _invariants import check_episode

    from podcast_agent.transcript import TranscriptError, parse_json_transcript

KEYS = ["transcript", "utterances", "host", "guests", "title", "episode_id"]
ROW_KEYS = ["timestamp", "time", "speaker", "text", "section"]


def value(fdp: atheris.FuzzedDataProvider, depth: int = 0):
    kind = fdp.ConsumeIntInRange(0, 7 if depth < 3 else 4)
    if kind == 0:
        return None
    if kind == 1:
        return fdp.ConsumeBool()
    if kind == 2:
        return fdp.ConsumeInt(4)
    if kind == 3:
        return fdp.ConsumeRegularFloat()
    if kind == 4:
        return fdp.ConsumeUnicodeNoSurrogates(fdp.ConsumeIntInRange(0, 40))
    if kind == 5:
        return [value(fdp, depth + 1) for _ in range(fdp.ConsumeIntInRange(0, 4))]
    if kind == 6:
        return {fdp.PickValueInList(ROW_KEYS): value(fdp, depth + 1) for _ in range(fdp.ConsumeIntInRange(0, 5))}
    return {fdp.ConsumeUnicodeNoSurrogates(8): value(fdp, depth + 1) for _ in range(fdp.ConsumeIntInRange(0, 3))}


def row(fdp: atheris.FuzzedDataProvider):
    if fdp.ConsumeProbability() < 0.1:
        return value(fdp, 1)
    return {k: (fdp.ConsumeUnicodeNoSurrogates(24) if fdp.ConsumeBool() else value(fdp, 1))
            for k in ROW_KEYS if fdp.ConsumeBool()}


def build(fdp: atheris.FuzzedDataProvider):
    rows = [row(fdp) for _ in range(fdp.ConsumeIntInRange(0, 6))]
    if fdp.ConsumeProbability() < 0.15:
        return rows
    doc = {k: value(fdp, 1) for k in KEYS[2:] if fdp.ConsumeBool()}
    doc[fdp.PickValueInList(KEYS[:2])] = rows if fdp.ConsumeProbability() < 0.9 else value(fdp, 1)
    return doc


def TestOneInput(data: bytes) -> None:
    fdp = atheris.FuzzedDataProvider(data)
    if fdp.ConsumeBool():
        try:
            doc = json.loads(fdp.ConsumeUnicodeNoSurrogates(len(data)))
        except (ValueError, RecursionError):
            return  # not JSON: rejected before the parser ever sees it
        if not isinstance(doc, (dict, list)):
            return  # the API's request model only admits an object or an array here
    else:
        doc = build(fdp)
    rows = doc.get("transcript") or doc.get("utterances") if isinstance(doc, dict) else doc
    try:
        episode = parse_json_transcript(doc, fallback_id="fuzz")
    except TranscriptError:
        return
    check_episode(episode, rows=len(rows))


def main() -> None:
    atheris.Setup(sys.argv, TestOneInput)
    atheris.Fuzz()


if __name__ == "__main__":
    main()
