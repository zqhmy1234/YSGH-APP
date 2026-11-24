"""The single response/error envelope for agent-facing endpoints.

Every successful agent endpoint returns ``AgentEnvelope[T]``. Errors use the
existing backend envelope ``{code,message,request_id,details}`` and never leak a
stack trace. The agent adapter maps both shapes to port errors in one place.
"""

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class AgentError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    request_id: str = ""
    details: dict | None = None


class AgentEnvelope(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid", frozen=True)

    data: T
    request_id: str = ""
