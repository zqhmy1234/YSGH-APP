"""EchoPort: today / preferences / exclusions. Silent by default."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

EchoStatus = Literal["ok", "suppressed", "no_candidate", "unavailable"]


@dataclass(frozen=True)
class EchoCandidate:
    echo_id: str
    title: str
    reason: str
    sensitivity: str
    score: float
    why_now: str


@dataclass(frozen=True)
class EchoResult:
    status: EchoStatus
    candidate: EchoCandidate | None = None
    suppressed_reason: str = ""


@runtime_checkable
class EchoPort(Protocol):
    def today(self, user_id: str) -> EchoResult: ...

    def dismiss(self, user_id: str, echo_id: str) -> bool: ...
