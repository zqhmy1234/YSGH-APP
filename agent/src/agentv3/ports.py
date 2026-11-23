"""Ports: the only surface a harness module may talk to.

Rules enforced by ``tests/agentv3/test_boundary.py``:
  * ``harness``/``agents``/``memory`` import ``contracts`` and ``ports`` only;
  * they never import ``adapters`` (that is the composition root's job);
  * they never import sqlalchemy / httpx / qdrant / langchain.

Every port raises ``PortError`` (or a subclass) when it cannot answer. A port
must never return a made-up answer to hide a failure: the orchestrator turns
``PortError`` into ``degraded`` instead.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Protocol, Sequence, runtime_checkable

from .contracts import (
    ClaimBundle,  # noqa: F401  (re-exported for typing convenience)
    CommitReceipt,
    Digest,
    EventHit,
    GuardRequest,
    GuardVerdict,
    Hit,
    LLMRequest,
    LLMResponse,
    MemoryEntry,
    MemoryProposal,
    ProposalBatch,
    PurgeReceipt,
    PurgeScope,
    SearchQuery,
    SearchResult,
    WriteReceipt,
)


class PortError(RuntimeError):
    """A port could not answer. Callers must degrade, never invent."""

    def __init__(self, port: str, detail: str = "") -> None:
        super().__init__(f"{port}: {detail}" if detail else port)
        self.port = port
        self.detail = detail


class PortTimeout(PortError):
    pass


class PortUnavailable(PortError):
    pass


class ConflictError(PortError):
    """A human correction outranks this write (never silently overwritten)."""


@runtime_checkable
class LLMPort(Protocol):
    def complete(self, request: LLMRequest) -> LLMResponse: ...


@runtime_checkable
class SearchPort(Protocol):
    def keyword(self, query: SearchQuery, *, alternates: Sequence[str]) -> list[Hit]: ...

    def dense(self, query: SearchQuery) -> list[Hit]: ...

    def sparse(self, query: SearchQuery) -> list[Hit]: ...

    def window(self, query: SearchQuery) -> list[Hit]: ...


@runtime_checkable
class EventPort(Protocol):
    def hits(self, person_id: str, *, from_: date | None, to: date | None, keywords: Sequence[str]) -> list[Hit]: ...


@runtime_checkable
class TimelinePort(Protocol):
    def between(self, person_id: str, from_: date, to: date, limit: int = 50) -> list[Hit]: ...


@runtime_checkable
class PersonPort(Protocol):
    def exists(self, person_id: str) -> bool: ...


@runtime_checkable
class AssetPort(Protocol):
    def snapshot(self, content_id: str) -> dict[str, str] | None: ...


@runtime_checkable
class DigestPort(Protocol):
    def build(self, person_id: str, budget_chars: int) -> Digest: ...


@runtime_checkable
class MemoryReadPort(Protocol):
    def entries(self, person_id: str, layers: Sequence[str] | None = None) -> list[MemoryEntry]: ...

    def digest_facts(self, person_id: str, limit: int = 40) -> list[MemoryEntry]: ...


@runtime_checkable
class MemoryWritePort(Protocol):
    def propose(self, proposal: MemoryProposal, *, person_id: str) -> WriteReceipt: ...

    def commit(self, batch: ProposalBatch) -> CommitReceipt: ...

    def purge(self, scope: PurgeScope) -> PurgeReceipt: ...


@runtime_checkable
class GuardPort(Protocol):
    def screen(self, request: GuardRequest) -> GuardVerdict: ...


@runtime_checkable
class ClockPort(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class TracePort(Protocol):
    def span(self, name: str, **fields: str | int | float | bool) -> "Span": ...


class Span(Protocol):
    def __enter__(self) -> "Span": ...

    def __exit__(self, *exc: object) -> None: ...

    @property
    def ms(self) -> int: ...
