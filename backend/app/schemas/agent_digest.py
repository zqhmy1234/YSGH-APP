"""Agent-facing DTOs for L3 endpoints.

The wire truth for agent ↔ backend lives in ``shared/contracts``. These Pydantic
models mirror the same fields for FastAPI schema generation; a contract test
asserts the field sets never diverge.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentDigestSection(_Strict):
    name: str
    text: str


class AgentDigestOut(_Strict):
    status: Literal["ok", "insufficient_data", "unavailable"]
    text: str = ""
    chars: int = Field(default=0, ge=0, le=600)
    sections: list[AgentDigestSection] = Field(default_factory=list)


class PersonAgentCreate(_Strict):
    idempotency_key: str = Field(min_length=8, max_length=64)
    display_name: str = Field(min_length=1, max_length=80)
    relation: str = Field(default="other", max_length=32)
    birth_date: date | None = None
    notes: str = Field(default="", max_length=2000)


class PersonAgentOut(_Strict):
    person_id: str
    display_name: str
    relation: str
    birth_date: date | None = None
    source: str = "user"
    confidence: float = 1.0


class EchoAgentRequest(_Strict):
    person_id: str = Field(min_length=1, max_length=64)
    local_date: date
    limit: int = Field(default=1, ge=1, le=5)


class EchoAgentCandidate(_Strict):
    echo_id: str
    person_id: str
    title: str
    reason: Literal["anniversary", "season", "place", "person", "topic"]
    sensitivity: Literal["public", "personal", "sensitive", "critical"]
    score: float = Field(ge=0.0, le=1.0)
    why_now: str


class EchoAgentResponse(_Strict):
    items: list[EchoAgentCandidate] = Field(default_factory=list)
    suppressed: str = ""


class AgentDigestStatusOut(_Strict):
    """Internal health shape for agent adapters; never a 4xx for missing profile."""

    status: Literal["ok", "insufficient_data", "unavailable"]
    chars: int = 0
    sections: int = 0
