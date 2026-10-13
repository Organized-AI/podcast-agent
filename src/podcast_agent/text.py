"""Small, dependency-free text utilities: tokenizing, BM25 ranking, fuzzy span matching."""

from __future__ import annotations

import math
import re
from collections import Counter

from rapidfuzz import fuzz

STOPWORDS = frozenset(
    ["a", "about", "above", "after", "again", "against", "all", "also", "am", "an", "and", "any", "are", "aren't", "as", "at", "be", "because", "been", "before", "being", "below", "between", "both", "but", "by", "can", "can't", "cannot", "could", "couldn't", "did", "didn't", "do", "does", "doesn't", "doing", "don't", "down", "during", "each", "few", "for", "from", "further", "get", "got", "had", "hadn't", "has", "hasn't", "have", "haven't", "having", "he", "he'd", "he'll", "he's", "her", "here", "here's", "hers", "herself", "him", "himself", "his", "how", "how's", "i", "i'd", "i'll", "i'm", "i've", "if", "in", "into", "is", "isn't", "it", "it's", "its", "itself", "just", "let's", "me", "more", "most", "mustn't", "my", "myself", "no", "nor", "not", "now", "of", "off", "on", "once", "only", "or", "other", "ought", "our", "ours", "ourselves", "out", "over", "own", "really", "same", "say", "says", "said", "shan't", "she", "she'd", "she'll", "she's", "should", "shouldn't", "so", "some", "such", "than", "that", "that's", "the", "their", "theirs", "them", "themselves", "then", "there", "there's", "these", "they", "they'd", "they'll", "they're", "they've", "this", "those", "through", "to", "too", "under", "until", "up", "us", "very", "was", "wasn't", "we", "we'd", "we'll", "we're", "we've", "were", "weren't", "what", "what's", "when", "when's", "where", "where's", "which", "while", "who", "who's", "whom", "why", "why's", "will", "with", "won't", "would", "wouldn't", "yes", "yet", "you", "you'd", "you'll", "you're", "you've", "your", "yours", "yourself", "yourselves", "thanks", "thank", "okay", "ok", "well", "yeah", "great", "absolutely", "lot", "lots", "one", "ones", "thing", "things", "way", "much", "many", "even", "still", "like", "just", "quite"]
)


def normalize_quotes(text: str) -> str:
    return (
        text.replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
        .replace("—", "-").replace("–", "-").replace("…", "...")
    )


def stem(word: str) -> str:
    """Deliberately tiny suffix stripper: enough for 'handbooks'~'handbook', 'reviews'~'review'."""
    if len(word) <= 4 or word.isdigit():
        return word
    for suffix, repl in (("ies", "y"), ("sses", "ss"), ("ing", ""), ("ed", ""), ("es", "e"), ("s", "")):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            if suffix == "s" and word.endswith("ss"):
                return word
            return word[: len(word) - len(suffix)] + repl
    return word


def tokenize(text: str, keep_stopwords: bool = False) -> list[str]:
    text = normalize_quotes(text).lower()
    text = re.sub(r"(?<=\d),(?=\d{3})", "", text)  # 200,000 -> 200000
    text = re.sub(r"\b(\d+)k\b", r"\g<1>000", text)  # 200k -> 200000
    tokens = re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text)
    return [stem(t) for t in tokens if keep_stopwords or (t not in STOPWORDS and len(t) > 1)]


def word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'’-]+\b", text))


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[\"'“A-Z0-9])", text.strip())
    return [p.strip() for p in parts if p.strip()]


def kebab(text: str) -> str:
    text = normalize_quotes(text).lower().lstrip("#")
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def span_match(needle: str, haystack: str) -> float:
    """0–1 similarity of `needle` to the best-matching contiguous span of `haystack`."""
    a = normalize_quotes(needle).lower().strip(" .\"'")
    b = normalize_quotes(haystack).lower()
    if not a:
        return 0.0
    if a in b:
        return 1.0
    return fuzz.partial_ratio(a, b) / 100.0


class BM25:
    """Okapi BM25 over pre-tokenized documents."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.docs = docs
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.avgdl = (sum(len(d) for d in docs) / self.n) if self.n else 0.0
        self.tf = [Counter(d) for d in docs]
        df: Counter[str] = Counter()
        for d in docs:
            df.update(set(d))
        self.idf = {t: math.log(1 + (self.n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def score(self, query: list[str], i: int) -> float:
        tf, dl = self.tf[i], len(self.docs[i])
        total = 0.0
        for t in query:
            if t not in tf:
                continue
            f = tf[t]
            total += self.idf.get(t, 0.0) * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1)))
        return total

    def coverage(self, query: list[str], i: int) -> float:
        """Share of the query's IDF weight that appears in document i (0–1). Easier to read than raw BM25."""
        q = set(query)
        weights = {t: self.idf.get(t, math.log(1 + self.n + 0.5)) for t in q}
        total = sum(weights.values())
        if not total:
            return 0.0
        return sum(w for t, w in weights.items() if t in self.tf[i]) / total

    def top(self, query: list[str], k: int = 3) -> list[tuple[int, float, float]]:
        scored = [(i, self.score(query, i), self.coverage(query, i)) for i in range(self.n)]
        scored = [s for s in scored if s[1] > 0]
        scored.sort(key=lambda s: s[1], reverse=True)
        return scored[:k]
