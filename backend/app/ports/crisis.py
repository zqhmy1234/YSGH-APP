"""CrisisPort: T1/T2/T3 assessment. T3 short-circuits before the agent."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

CrisisLevel = Literal["none", "t1", "t2", "t3"]


@dataclass(frozen=True)
class CrisisSignal:
    level: CrisisLevel
    reason: str = ""
    resources: tuple[str, ...] = ()


@dataclass(frozen=True)
class CrisisRecord:
    level: CrisisLevel
    record_status: Literal["recorded", "failed", "not_applicable"]
    escalation_note: str = ""


@runtime_checkable
class CrisisPort(Protocol):
    def scan(self, text: str) -> CrisisSignal: ...

    def record(self, user_id: str, signal: CrisisSignal) -> CrisisRecord: ...
