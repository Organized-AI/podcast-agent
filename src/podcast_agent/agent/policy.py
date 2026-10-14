"""Verdict policy that code enforces, so it does not depend on the model following the prompt.

A prediction can only be checked once its deadline has passed. "Bitcoin will reach $200K by 2025" is
checkable in 2026; "Humans will walk on Mars before 2040" is not, however unlikely it looks today, and
neither is an open-ended "async culture will become standard practice".
"""

from __future__ import annotations

import re
from typing import Literal

DeadlineStatus = Literal["passed", "pending", "none"]

_YEAR = re.compile(r"(?i)\b(early|mid|late|end of)?\s*(20\d{2})\b")


def deadline_status(text: str, today: str) -> DeadlineStatus:
    """'passed' if any year in the text (read as the end of that period) is before today, 'pending' if
    every year is still ahead, 'none' if the text names no year."""
    year_now, month_now = int(today[:4]), int(today[5:7])
    found = False
    for m in _YEAR.finditer(text):
        found = True
        qualifier, year = (m.group(1) or "").lower(), int(m.group(2))
        end_month = {"early": 6, "mid": 9}.get(qualifier, 12)
        if (year, end_month) < (year_now, month_now):
            return "passed"
    return "pending" if found else "none"


def prediction_is_checkable(claim_type: str, text: str, today: str) -> bool:
    """Only predictions are restricted; any other claim type is always checkable."""
    return claim_type != "prediction" or deadline_status(text, today) == "passed"
