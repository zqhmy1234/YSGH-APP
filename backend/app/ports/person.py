"""PersonPort: only user-confirmed persons; no AI-inferred rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol, runtime_checkable

Epistemic = Literal["user_confirmed", "imported"]


@dataclass(frozen=True)
class Person:
    person_id: str
    user_id: str
    display_name: str
    relation: str = "other"
    birth_date: date | None = None
    source: str = "user"
    confidence: float = 1.0
    epistemic: Epistemic = "user_confirmed"


@runtime_checkable
class PersonPort(Protocol):
    def get(self, user_id: str, person_id: str) -> Person | None: ...

    def list(self, user_id: str, *, limit: int = 50, cursor: str = "") -> tuple[list[Person], str]: ...

    def create(self, person: Person, *, idempotency_key: str) -> Person: ...

    def soft_delete(self, user_id: str, person_id: str) -> bool: ...
