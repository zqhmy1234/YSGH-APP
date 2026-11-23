"""Persona API contracts (output only; input is the domain ``PersonaUpdate``)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.persona import RelationshipChoices


class PersonaOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    choices: RelationshipChoices
    role_id: str
    version: int
    updated_at: datetime | None = None
    source: str
    relationship_note: str


class PersonaChoiceQuestionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str
    options: tuple[str, ...]


class RoleCardOptionOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str
    display_name: str
    intro: str


class PersonaOptionsOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    questions: tuple[PersonaChoiceQuestionOut, ...]
    role_cards: tuple[RoleCardOptionOut, ...]


class PersonaUpdateIn(BaseModel):
    """Request body for PUT: three choices plus the selected person."""

    model_config = ConfigDict(extra="forbid")

    choices: RelationshipChoices = Field(default_factory=RelationshipChoices)
    role_id: str = Field(default="gentle-friend", min_length=1, max_length=64)


__all__ = [
    "PersonaChoiceQuestionOut",
    "PersonaOptionsOut",
    "PersonaOut",
    "PersonaUpdateIn",
    "RoleCardOptionOut",
]
