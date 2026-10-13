"""Web search tool: a mock engine over a local fixture (default) or Tavily (optional).

The mock behaves like a real search API — ranked results with publish dates, configurable
latency, and injectable failures (MOCK_SEARCH_FAILURE_RATE) so retry and degradation paths can
be exercised on demand.
"""

from __future__ import annotations

import json
import random
import time
from abc import ABC, abstractmethod
from pathlib import Path

import httpx
from pydantic import BaseModel

from podcast_agent.text import BM25, tokenize


class ToolError(RuntimeError):
    pass


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    published: str | None = None
    relevance: float = 0.0


class SearchTool(ABC):
    name = "web_search"

    @abstractmethod
    def search(self, query: str, k: int = 3) -> list[SearchResult]: ...


class MockWebSearch(SearchTool):
    provider = "mock"

    def __init__(self, path: str | Path, latency_ms: int = 0, failure_rate: float = 0.0, seed: int = 7):
        path = Path(path)
        rows = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
        self.pages = [SearchResult.model_validate(r) for r in (rows["pages"] if isinstance(rows, dict) else rows)]
        self._index = BM25([tokenize(f"{p.title} {p.snippet}") for p in self.pages])
        self.latency_ms = latency_ms
        self.failure_rate = failure_rate
        self._rng = random.Random(seed)

    def search(self, query: str, k: int = 3) -> list[SearchResult]:
        if self.latency_ms:
            time.sleep(self.latency_ms / 1000)
        if self.failure_rate and self._rng.random() < self.failure_rate:
            raise ToolError("simulated upstream timeout from search provider")
        hits = self._index.top(tokenize(query), k=k)
        return [self.pages[i].model_copy(update={"relevance": round(c, 3)}) for i, _, c in hits]


class TavilySearch(SearchTool):
    """Real web search via Tavily (set SEARCH_PROVIDER=tavily and TAVILY_API_KEY)."""

    provider = "tavily"

    def __init__(self, api_key: str, timeout_s: float = 20):
        self._http = httpx.Client(timeout=timeout_s, headers={"Authorization": f"Bearer {api_key}"})

    def search(self, query: str, k: int = 3) -> list[SearchResult]:
        try:
            r = self._http.post("https://api.tavily.com/search", json={"query": query, "max_results": k})
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolError(f"Tavily search failed: {exc}") from exc
        out = []
        for row in r.json().get("results", [])[:k]:
            out.append(
                SearchResult(
                    title=row.get("title", ""),
                    url=row.get("url", ""),
                    snippet=(row.get("content") or "")[:600],
                    published=row.get("published_date"),
                    relevance=round(float(row.get("score") or 0.0), 3),
                )
            )
        return out


def with_retries(fn, *, attempts: int = 3, base_delay: float = 0.25, on_retry=None):
    """Call fn(); retry ToolError with exponential backoff. Re-raises the last error."""
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except ToolError as exc:
            if attempt == attempts:
                raise
            if on_retry:
                on_retry(attempt, exc)
            time.sleep(base_delay * 2 ** (attempt - 1))
