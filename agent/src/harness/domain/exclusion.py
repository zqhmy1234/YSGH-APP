"""User-owned exclusions: people, words and date ranges that must never surface."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date

from harness.contracts import Episode


class ExclusionPolicy:
    def __init__(
        self,
        people: Iterable[str] = (),
        words: Iterable[str] = (),
        ranges: Sequence[tuple[date, date]] = (),
    ) -> None:
        self._people = {p.strip() for p in people if p.strip()}
        self._words = {w.strip() for w in words if w.strip()}
        self._ranges = tuple(ranges)

    def blocked_reason(self, episode: Episode) -> str | None:
        if any(person in self._people for person in episode.people):
            return "blocked_person"
        if any(word in episode.text for word in self._words):
            return "blocked_word"
        day = episode.day
        if day and any(start <= day <= end for start, end in self._ranges):
            return "blocked_range"
        return None

    def filter(self, episodes: Sequence[Episode]) -> list[Episode]:
        return [ep for ep in episodes if self.blocked_reason(ep) is None]
