"""shared/contracts must match the backend FastAPI schemas field-for-field.

If either side adds/removes a field, the agent and backend wire shapes diverge
silently. This is the single guard; it fails loudly rather than letting the
adapter parse a subset and drop the new field.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from app.schemas.agent_digest import (  # noqa: E402
    AgentDigestOut,
    AgentDigestSection,
    EchoAgentCandidate,
    EchoAgentRequest,
    EchoAgentResponse,
    PersonAgentCreate,
    PersonAgentOut,
)

from shared.contracts.agent_api import (  # noqa: E402
    AgentDigest,
    AgentEchoCandidate,
    AgentEchoRequest,
    AgentEchoResponse,
    AgentPerson,
    AgentPersonCreate,
)


def _fields(model) -> set[str]:
    return set(model.model_fields)


def test_digest_shapes_match():
    assert _fields(AgentDigest) == _fields(AgentDigestOut)
    assert _fields(AgentDigest) >= {"status", "text", "chars"}


def test_digest_section_matches():
    assert _fields(AgentDigestSection) == {"name", "text"}


def test_person_shapes_match():
    assert _fields(PersonAgentCreate) == _fields(AgentPersonCreate)
    assert _fields(PersonAgentOut) == _fields(AgentPerson)


def test_echo_shapes_match():
    assert _fields(EchoAgentRequest) == _fields(AgentEchoRequest)
    assert _fields(EchoAgentCandidate) == _fields(AgentEchoCandidate)
    assert _fields(EchoAgentResponse) == _fields(AgentEchoResponse)
