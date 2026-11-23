"""Agent registry: capability, permission and cost ceiling per agent.

The orchestrator never decides *how* an agent works; it only decides *whether*
it may run and with what budget. Adding an agent means adding one spec here.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import AgentName


@dataclass(frozen=True)
class AgentSpec:
    name: AgentName
    may_write: bool
    may_speak: bool
    max_ms: int
    max_chars: int
    required_ports: tuple[str, ...]
    trigger: str


SPECS: dict[AgentName, AgentSpec] = {
    AgentName.MUSE: AgentSpec(
        name=AgentName.MUSE,
        may_write=False,
        may_speak=True,
        max_ms=3000,
        max_chars=2200,
        required_ports=("llm",),
        trigger="every turn (the only agent that speaks)",
    ),
    AgentName.LIBRARIAN: AgentSpec(
        name=AgentName.LIBRARIAN,
        may_write=False,
        may_speak=False,
        max_ms=800,
        max_chars=1600,
        required_ports=("search", "event"),
        trigger="recall intent or a required evidence lookup",
    ),
    AgentName.PROVENANCE: AgentSpec(
        name=AgentName.PROVENANCE,
        may_write=False,
        may_speak=False,
        max_ms=800,
        max_chars=800,
        required_ports=("llm",),
        trigger="triggered: private/numeric/date/name claim present",
    ),
    AgentName.SCULPTOR: AgentSpec(
        name=AgentName.SCULPTOR,
        may_write=True,
        may_speak=False,
        max_ms=1000,
        max_chars=1000,
        required_ports=("memory_write", "llm"),
        trigger="remember intent (propose) / nightly batch (commit)",
    ),
    AgentName.SERENDIPITY: AgentSpec(
        name=AgentName.SERENDIPITY,
        may_write=False,
        may_speak=False,
        max_ms=600,
        max_chars=1000,
        required_ports=("search",),
        trigger="idle prefetch only; never writes, never speaks",
    ),
}


def spec(name: AgentName) -> AgentSpec:
    return SPECS[name]


def assert_can_write(name: AgentName) -> None:
    if not SPECS[name].may_write:
        raise PermissionError(f"{name.value} must not write to memory")
