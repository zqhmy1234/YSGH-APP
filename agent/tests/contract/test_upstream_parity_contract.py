"""Linger parity, contract-test entry (plan DoD path ``tests/contract/``)."""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agentv3.agents.librarian import RRF_K  # noqa: E402
from agentv3.agents.sculptor import ISSUE_CODES  # noqa: E402
from agentv3.contracts import MAX_ALTERNATES  # noqa: E402


def test_upstream_constants_are_pinned():
    assert RRF_K == 60
    assert MAX_ALTERNATES == 6
    assert len(ISSUE_CODES) == 19
