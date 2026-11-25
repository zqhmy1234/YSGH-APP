"""MemoryPort: read model + two-phase write, three-state failures.

``MemorySnapshot`` is the only shape the agent may see; it never exposes ORM
rows. ``unavailable`` means storage is down; ``insufficient_data`` means the
user has no memories yet. They are never both an empty list.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

MemoryStatus = Literal["ok", "insufficient_data", "unavailable"]
WriteState = Literal["draft", "committed", "rejected", "conflict", "unavailable"]


@dataclass(frozen=True)
class NarrativeEpisode:
    entry_id: str
    text: str
    evidence: tuple[str, ...] = ()
    valid_from: str = ""
    story_arc: str = ""


@dataclass(frozen=True)
class Correction:
    target_type: str
    target_id: str
    field: str
    value: str


@dataclass(frozen=True)
class MemorySnapshot:
    # ``user_id`` is the account boundary. The old V3 ``person_id`` name was
    # ambiguous (it sometimes meant the account, sometimes a family member) and
    # is deliberately not part of the canonical port.
    user_id: str
    status: MemoryStatus
    narrative: tuple[NarrativeEpisode, ...] = ()
    corrections: tuple[Correction, ...] = ()
    digest: dict | None = None
    asset_index: tuple[dict, ...] = ()
    note: str = field(default="")


@dataclass(frozen=True)
class WriteReceipt:
    state: WriteState
    entry_id: str = ""
    reason: str = ""
    idempotent: bool = False


@runtime_checkable
class MemoryPort(Protocol):
    def read(self, user_id: str) -> MemorySnapshot: ...

    def propose(self, user_id: str, *, text: str, evidence: tuple[str, ...], idempotency_key: str) -> WriteReceipt: ...

    def commit(self, user_id: str, *, batch_hash: str) -> WriteReceipt: ...

    def purge(self, user_id: str, *, entry_ids: tuple[str, ...] = ()) -> int: ...
