"""Local knowledge base: curated, dated facts the agency trusts, ranked with BM25.

Each entry carries an ``as_of`` date so the agent can reason about staleness — the difference
between ✅ "verified" and ⚠️ "possibly outdated" is often just whether the evidence is current.
In production this becomes Postgres + pgvector with hybrid (BM25 + embedding) retrieval.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from podcast_agent.text import BM25, tokenize


class KBEntry(BaseModel):
    id: str
    title: str
    content: str
    source: str
    url: str | None = None
    as_of: str
    tags: list[str] = Field(default_factory=list)


class KBHit(BaseModel):
    entry: KBEntry
    score: float
    relevance: float  # 0–1 query-term coverage


class KnowledgeBase:
    def __init__(self, entries: list[KBEntry]):
        self.entries = entries
        self._index = BM25([tokenize(f"{e.title} {e.title} {' '.join(e.tags)} {e.content}") for e in entries])

    @classmethod
    def load(cls, path: str | Path) -> KnowledgeBase:
        path = Path(path)
        if not path.exists():
            return cls([])
        data = json.loads(path.read_text(encoding="utf-8"))
        rows = data["entries"] if isinstance(data, dict) else data
        return cls([KBEntry.model_validate(r) for r in rows])

    def __len__(self) -> int:
        return len(self.entries)

    def search(self, query: str, k: int = 3) -> list[KBHit]:
        if not self.entries:
            return []
        hits = self._index.top(tokenize(query), k=k)
        return [KBHit(entry=self.entries[i], score=round(s, 3), relevance=round(c, 3)) for i, s, c in hits]
