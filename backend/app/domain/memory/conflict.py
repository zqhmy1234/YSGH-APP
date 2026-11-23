"""Conflict precedence: user correction > confirmed fact > latest > high confidence.

When none of the first three decides, values are kept side by side as
``pending_confirmation``. There is no automatic silent winner.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Winner = Literal["correction", "confirmed_fact", "latest", "confidence", "pending_confirmation"]


@dataclass(frozen=True)
class Candidate:
    value: str
    source: Literal["correction", "confirmed_fact", "inference"]
    recorded_at: int = 0
    confidence: float = 0.0


def resolve(candidates: list[Candidate]) -> tuple[Winner, list[Candidate]]:
    corrections = [c for c in candidates if c.source == "correction"]
    if corrections:
        return "correction", corrections
    confirmed = [c for c in candidates if c.source == "confirmed_fact"]
    if len(confirmed) == 1:
        return "confirmed_fact", confirmed
    if len(confirmed) > 1:
        newest = max(confirmed, key=lambda c: c.recorded_at)
        newest_rows = [c for c in confirmed if c.recorded_at == newest.recorded_at]
        if len(newest_rows) == 1:
            return "latest", newest_rows
        # Same timestamp but different values: keep side by side, never guess.
        if len({c.value for c in newest_rows}) > 1:
            return "pending_confirmation", newest_rows
        return "latest", newest_rows[:1]
    if len(candidates) == 1:
        return "confidence", candidates
    return "pending_confirmation", candidates


__all__ = ["Candidate", "Winner", "resolve"]
