"""Typed loaders for the eval datasets in ``evals/datasets``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

VerdictName = Literal["verified", "possibly_outdated_or_inaccurate", "unverifiable"]


class FactCheckCase(BaseModel):
    id: str
    claim: str
    type: Literal["factual", "statistical", "historical", "prediction", "anecdotal"]
    time_sensitive: bool = False
    episode: str | None = None
    today: str
    expected: VerdictName
    acceptable: list[VerdictName]
    why: str
    kb: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class SummaryExpectation(BaseModel):
    words: tuple[int, int]
    mentions: list[list[str]] = Field(default_factory=list)


class NotesExpectation(BaseModel):
    takeaways: int = 5
    quotes: tuple[int, int] = (3, 5)
    topics: tuple[int, int] = (4, 10)


class ClaimExpectation(BaseModel):
    match: list[str]
    acceptable: list[VerdictName]


class EpisodeExpectation(BaseModel):
    transcript: str
    summary: SummaryExpectation
    notes: NotesExpectation = Field(default_factory=NotesExpectation)
    claims: list[ClaimExpectation] = Field(default_factory=list)


def load_factcheck_cases(path: str | Path) -> list[FactCheckCase]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return [FactCheckCase.model_validate(json.loads(line)) for line in lines if line.strip()]


def load_episode_expectations(path: str | Path) -> dict[str, EpisodeExpectation]:
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return {eid: EpisodeExpectation.model_validate(exp) for eid, exp in doc["episodes"].items()}
