"""PersonaPort: a user-owned relationship snapshot, read once per turn.

The card carries who she is; this port carries how she has agreed to relate to
this person. The backend has already turned the three choices into full prose,
so the agent never sees (or re-renders) 0/1/2 positions, and never mutates a
process-wide persona.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field


class RelationshipChoices(BaseModel):
    """Three relations, each a 0/1/2 position in the backend UI only."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    initiative: int = Field(default=0, ge=0, le=2)
    density: int = Field(default=0, ge=0, le=2)
    advice_bias: int = Field(default=0, ge=0, le=2)


class PersonaSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: str
    role_id: str = "gentle-friend"
    relationship_note: str = ""
    choices: RelationshipChoices = Field(default_factory=RelationshipChoices)
    version: int = Field(default=0, ge=0)
    updated_at: datetime | None = None
    source: str = "default"


@runtime_checkable
class PersonaPort(Protocol):
    def snapshot(self, user_id: str) -> PersonaSnapshot: ...


class DefaultPersona:
    """Deterministic fallback used when the backend has no persona row."""

    def __init__(self, role_id: str = "gentle-friend", relationship_note: str = "") -> None:
        self._role_id = role_id
        self._relationship_note = relationship_note

    def snapshot(self, user_id: str) -> PersonaSnapshot:
        return PersonaSnapshot(
            user_id=user_id,
            role_id=self._role_id,
            relationship_note=self._relationship_note,
        )


__all__ = ["DefaultPersona", "PersonaPort", "PersonaSnapshot", "RelationshipChoices"]
