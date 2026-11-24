"""SearchPort: one normalized query, three channels, one filter set."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

SearchStatus = Literal["ok", "no_match", "not_indexed", "degraded"]


@dataclass(frozen=True)
class SearchQuery:
    q: str
    user_id: str
    content_types: tuple[str, ...] = ()
    time_from: str = ""
    time_to: str = ""
    place: str = ""
    tag: str = ""
    limit: int = 10


@dataclass(frozen=True)
class SearchHit:
    content_id: str
    content_type: str
    text: str
    score: float
    trace: dict = field(default_factory=dict)
    thumbnail_url: str = ""


@dataclass(frozen=True)
class SearchResult:
    status: SearchStatus
    hits: tuple[SearchHit, ...] = ()
    trace: tuple[dict, ...] = ()
    degraded: bool = False
    reason: str = ""


@runtime_checkable
class SearchPort(Protocol):
    def search(self, query: SearchQuery) -> SearchResult: ...
