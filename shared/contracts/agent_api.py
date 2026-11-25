"""Wire contracts for the six agent <-> backend HTTP endpoints.

Design rules (plan §3.4 / 45 §6.3):
  * failures are at least three-state; an empty list never doubles as an error;
  * writes carry an idempotency key;
  * unknown fields are rejected so drift is loud.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ------------------------------------------------------------------ search


class AgentSearchQuery(_Strict):
    query: str = Field(min_length=1, max_length=400)
    person_id: str = Field(min_length=1, max_length=64)
    mode: Literal["evidence", "overview", "person", "time"] = "evidence"
    alternates: tuple[str, ...] = ()
    date_from: date | None = None
    date_to: date | None = None
    event_from: date | None = None
    event_to: date | None = None
    limit: int = Field(ge=1, le=50, default=20)


class AgentSearchTrace(_Strict):
    claim: str = ""
    evidence_id: str = ""
    channel: str = ""


class AgentSearchHit(_Strict):
    cite_as: str = "S1"
    evidence_id: str
    kind: Literal["room", "recording", "document", "content", "event", "wiki", "period"] = "content"
    title: str
    time_hint: str = ""
    content: str = ""
    score: float = 0.0
    channel: Literal["keyword", "dense", "sparse", "event", "time"] = "keyword"
    source_kind: Literal["original", "summary"] = "original"


class AgentSearchResponse(_Strict):
    hits: tuple[AgentSearchHit, ...] = ()
    summaries: tuple[AgentSearchHit, ...] = ()
    reason: str = ""
    relaxed: bool = False
    clarify: str | None = None
    event_hit_count: int = 0
    trace: tuple[AgentSearchTrace, ...] = ()
    degraded: bool = False
    gaps: tuple[str, ...] = ()
    status: Literal["ok", "no_match", "not_indexed", "degraded"] = "ok"

    def to_agent_contract(self):
        """Convert to the harness ``SearchResult`` without importing harness here.

        ``shared`` is dependency-light by design; the conversion is lazy so this
        module never gains an import-time dependency on the agent implementation.
        The import path is ``harness.contracts`` (the post-refactor location).
        """
        from harness.contracts import Hit, ResultStatus, SearchResult, TraceItem

        status = {
            "ok": ResultStatus.GROUNDED,
            "no_match": ResultStatus.EMPTY,
            "not_indexed": ResultStatus.EMPTY,
            "degraded": ResultStatus.PARTIAL,
        }[self.status]
        return SearchResult(
            hits=tuple(Hit(**h.model_dump()) for h in self.hits),
            summaries=tuple(Hit(**h.model_dump()) for h in self.summaries),
            reason=self.reason,
            relaxed=self.relaxed,
            clarify=self.clarify,
            event_hit_count=self.event_hit_count,
            trace=tuple(TraceItem(**t.model_dump()) for t in self.trace),
            degraded=self.degraded,
            gaps=self.gaps,
            status=status,
        )


# ------------------------------------------------------------------ contents


class AgentContentCreate(_Strict):
    content_type: Literal["photo", "text", "voice", "article"]
    client_generated_id: str = Field(min_length=1, max_length=64)
    text: str | None = Field(default=None, max_length=20000)
    taken_at: datetime | None = None
    source: Literal["agent"] = "agent"
    tags: tuple[str, ...] = ()


class AgentContentDelete(_Strict):
    content_id: str = ""
    deleted: bool = False
    permanent_at: datetime | None = None


# ------------------------------------------------------------------ profile


AgentDigestStatus = Literal["ok", "insufficient_data", "unavailable"]


class AgentDigest(_Strict):
    status: AgentDigestStatus
    text: str = ""
    chars: int = Field(default=0, ge=0, le=600)
    sections: tuple[dict, ...] = ()


# ------------------------------------------------------------------ persons


class AgentPersonCreate(_Strict):
    idempotency_key: str = Field(min_length=8, max_length=64)
    display_name: str = Field(min_length=1, max_length=80)
    relation: str = Field(default="other", max_length=32)
    birth_date: date | None = None
    notes: str = Field(default="", max_length=2000)


class AgentPerson(_Strict):
    person_id: str
    display_name: str
    relation: str
    birth_date: date | None = None
    source: str = "user"
    confidence: float = 1.0


class AgentPersonList(_Strict):
    items: tuple[AgentPerson, ...] = ()
    next_cursor: str | None = None


# ------------------------------------------------------------------ events


class AgentEventHitsQuery(_Strict):
    person_id: str = Field(min_length=1, max_length=64)
    from_: date | None = Field(default=None, alias="from")
    to: date | None = None
    keywords: tuple[str, ...] = ()
    limit: int = Field(default=20, ge=1, le=50)

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AgentEventHit(_Strict):
    event_id: str
    title: str
    day: str = ""
    summary: str = ""
    score: float = 0.0


class AgentEventHitsResponse(_Strict):
    items: tuple[AgentEventHit, ...] = ()
    degraded: bool = False
    reason: str = ""


# ------------------------------------------------------------------ echo


class AgentEchoRequest(_Strict):
    person_id: str = Field(min_length=1, max_length=64)
    local_date: date
    limit: int = Field(default=1, ge=1, le=5)


class AgentEchoCandidate(_Strict):
    echo_id: str
    person_id: str
    title: str
    reason: Literal["anniversary", "season", "place", "person", "topic"]
    sensitivity: Literal["public", "personal", "sensitive", "critical"]
    score: float = Field(ge=0.0, le=1.0)
    why_now: str


class AgentEchoResponse(_Strict):
    items: tuple[AgentEchoCandidate, ...] = ()
    suppressed: str = ""
