"""Scoring functions. Pure: they take results and return numbers, so tests can pin them exactly."""

from __future__ import annotations

from collections.abc import Sequence

VERDICTS = ("verified", "possibly_outdated_or_inaccurate", "unverifiable")


def accuracy(correct: Sequence[bool]) -> float:
    return sum(correct) / len(correct) if correct else 0.0


def macro_f1(expected: Sequence[str], predicted: Sequence[str], labels: Sequence[str] = VERDICTS) -> float:
    """Unweighted mean of per-label F1, over the labels that occur in either sequence."""
    scores = []
    for label in labels:
        tp = sum(1 for e, p in zip(expected, predicted, strict=True) if e == label and p == label)
        fp = sum(1 for e, p in zip(expected, predicted, strict=True) if e != label and p == label)
        fn = sum(1 for e, p in zip(expected, predicted, strict=True) if e == label and p != label)
        if tp + fp + fn == 0:
            continue
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        scores.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
    return sum(scores) / len(scores) if scores else 0.0


def brier(confidences: Sequence[float], correct: Sequence[bool]) -> float:
    """Mean squared gap between the stated confidence and being right (0 is perfect, lower is better)."""
    if not confidences:
        return 0.0
    return sum((c - float(ok)) ** 2 for c, ok in zip(confidences, correct, strict=True)) / len(confidences)


def expected_calibration_error(confidences: Sequence[float], correct: Sequence[bool], bins: int = 5) -> float:
    """Average |accuracy - confidence| across equal-width confidence bins, weighted by bin size."""
    n = len(confidences)
    if not n:
        return 0.0
    total = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [i for i, c in enumerate(confidences) if lo <= c < hi or (b == bins - 1 and c == 1.0)]
        if idx:
            acc = sum(correct[i] for i in idx) / len(idx)
            conf = sum(confidences[i] for i in idx) / len(idx)
            total += len(idx) / n * abs(acc - conf)
    return total


def mention_coverage(text: str, groups: Sequence[Sequence[str]]) -> float:
    """Share of groups with at least one alternative present in ``text`` (case-insensitive substring)."""
    if not groups:
        return 1.0
    low = text.lower()
    return sum(1 for alts in groups if any(a.lower() in low for a in alts)) / len(groups)


def matches_all(text: str, words: Sequence[str]) -> bool:
    low = text.lower()
    return all(w.lower() in low for w in words)
