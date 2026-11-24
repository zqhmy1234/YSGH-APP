"""Frozen DTOs shared by backend and agent over HTTP.

These models are intentionally dependency-light (pydantic only). They describe
the wire shape of the six agent-facing backend endpoints and the five provider
ports. Neither side may import the other's implementation modules.

Plan reference: 架构重构总计划 §3.1, 45需求映射 §6.1/§6.3.
"""

from .agent_api import (
    AgentContentCreate,
    AgentContentDelete,
    AgentDigest,
    AgentDigestStatus,
    AgentEchoCandidate,
    AgentEchoRequest,
    AgentEchoResponse,
    AgentEventHit,
    AgentEventHitsQuery,
    AgentEventHitsResponse,
    AgentPerson,
    AgentPersonCreate,
    AgentPersonList,
    AgentSearchHit,
    AgentSearchQuery,
    AgentSearchResponse,
    AgentSearchTrace,
)
from .envelope import AgentEnvelope, AgentError

__all__ = [
    "AgentContentCreate",
    "AgentContentDelete",
    "AgentDigest",
    "AgentDigestStatus",
    "AgentEchoCandidate",
    "AgentEchoRequest",
    "AgentEchoResponse",
    "AgentEnvelope",
    "AgentError",
    "AgentEventHit",
    "AgentEventHitsQuery",
    "AgentEventHitsResponse",
    "AgentPerson",
    "AgentPersonCreate",
    "AgentPersonList",
    "AgentSearchHit",
    "AgentSearchQuery",
    "AgentSearchResponse",
    "AgentSearchTrace",
]
