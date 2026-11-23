"""Registry: five agents, fixed capability table, write permission asserted."""

import pytest

from harness.contracts import AgentName
from harness.registry import SPECS, assert_can_write, spec


def test_five_agents_are_registered():
    assert set(SPECS) == set(AgentName)


def test_only_sculptor_may_write():
    assert [name.value for name, s in SPECS.items() if s.may_write] == ["sculptor"]


def test_only_muse_may_speak():
    assert [name.value for name, s in SPECS.items() if s.may_speak] == ["muse"]


def test_assert_can_write_blocks_speaking_agents():
    with pytest.raises(PermissionError):
        assert_can_write(AgentName.MUSE)
    assert_can_write(AgentName.SCULPTOR) is None


def test_spec_lookup():
    assert spec(AgentName.LIBRARIAN).max_ms > 0
