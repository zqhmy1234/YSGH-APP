"""Serendipity: "one year ago today", precomputed and read-only.

It never writes and never blocks a turn. It receives a retrieval callable instead
of importing the Librarian, so the two agents stay independent and each can be
tested alone.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import date, timedelta

from harness.contracts import ResultStatus, SearchQuery, SearchResult
from ports import PortError

Retrieve = Callable[[SearchQuery], SearchResult]


class Serendipity:
    def __init__(self, retrieve: Retrieve) -> None:
        self._retrieve = retrieve
        self._cache: dict[tuple[str, str], SearchResult] = {}

    @staticmethod
    def year_ago(today: date, years: int = 1) -> date:
        try:
            return today.replace(year=today.year - years)
        except ValueError:  # 29 Feb
            return today.replace(year=today.year - years, day=28)

    def warm(self, person_id: str, *, topics: Iterable[str], today: date) -> dict[str, SearchResult]:
        out: dict[str, SearchResult] = {}
        anchor = self.year_ago(today)
        for topic in topics:
            query = SearchQuery(
                query=topic,
                person_id=person_id,
                mode="overview",
                event_from=anchor,
                event_to=anchor + timedelta(days=1),
            )
            try:
                result = self._retrieve(query)
            except PortError:
                continue
            if result.status is not ResultStatus.EMPTY:
                self._cache[(person_id, topic)] = result
                out[topic] = result
        return out

    def take(self, person_id: str, topic: str) -> SearchResult | None:
        return self._cache.get((person_id, topic))

    def hit_count(self) -> int:
        return len(self._cache)
