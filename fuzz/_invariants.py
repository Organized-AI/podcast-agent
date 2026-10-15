"""Shared checks for the fuzzers: what every parsed episode must satisfy, whatever the input was."""

from __future__ import annotations

import re

SAFE_ID = re.compile(r"^[A-Za-z0-9_](?:[A-Za-z0-9._-]{0,62}[A-Za-z0-9_])?$")


def check_episode(episode, rows: int | None = None) -> None:
    # The id names output files (<id>.md, <id>.json): it must never leave the output folder.
    assert SAFE_ID.match(episode.episode_id), f"unsafe episode id {episode.episode_id!r}"
    assert episode.utterances, "a parsed episode has at least one utterance"
    if rows is not None:
        assert len(episode.utterances) == rows, (len(episode.utterances), rows)
    for i, u in enumerate(episode.utterances):
        assert u.idx == i and u.seconds >= 0
