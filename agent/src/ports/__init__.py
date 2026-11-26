"""Agent-side port protocols (LLM/Backend/Checkpoint/Clock/Trace).

This module is the agent-side port layer; semantics moved from the legacy v3 package.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol, runtime_checkable

from harness.contracts import (
    ClaimBundle,  # noqa: F401  (re-exported for typing convenience)
    CommitReceipt as CommitReceipt,
    Digest,
    EventHit,
    GuardRequest as GuardRequest,
    GuardVerdict as GuardVerdict,
    Hit,
    LLMRequest,
    LLMResponse,
    MemoryEntry as MemoryEntry,
    MemoryProposal as MemoryProposal,
    ProposalBatch as ProposalBatch,
    PurgeReceipt as PurgeReceipt,
    PurgeScope as PurgeScope,
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
class PersonPort(Protocol):
    def exists(self, person_id: str) -> bool: ...


@runtime_checkable
class AssetPort(Protocol):
    def snapshot(self, content_id: str) -> dict[str, str] | None: ...


@runtime_checkable
class DigestPort(Protocol):
    def build(self, person_id: str, budget_chars: int) -> Digest: ...


@runtime_checkable
class BackendPort(Protocol):
    """The only facade the agent may use to read/write backend business data."""

    def search(self, query: SearchQuery) -> SearchResult: ...

    def create_content(self, payload: dict) -> WriteReceipt: ...

    def delete_content(self, client_generated_id: str) -> bool: ...

    def digest(self, person_id: str) -> Digest: ...

    def event_hits(
        self, person_id: str, *, from_: date | None, to: date | None, keywords: Sequence[str]
    ) -> list[EventHit]: ...


@runtime_checkable
class CheckpointPort(Protocol):
    def load(self, thread_id: str) -> dict: ...

    def save(self, thread_id: str, state: dict) -> None: ...


@runtime_checkable
class ClockPort(Protocol):
    def now(self) -> datetime: ...


@runtime_checkable
class TracePort(Protocol):
    def span(self, name: str, **fields: str | int | float | bool) -> Span: ...


class Span(Protocol):
    def __enter__(self) -> Span: ...

    def __exit__(self, *exc: object) -> None: ...

    @property
    def ms(self) -> int: ...
