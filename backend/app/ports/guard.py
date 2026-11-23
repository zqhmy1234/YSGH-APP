"""GuardPort: allow / mask / reject / handoff. No two-state booleans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

GuardAction = Literal["allow", "mask", "reject", "handoff"]


@dataclass(frozen=True)
class GuardVerdict:
    action: GuardAction
    reason: str = ""
    reply: str = ""


@runtime_checkable
class GuardPort(Protocol):
    def check_input(self, user_id: str, text: str) -> GuardVerdict: ...

    def check_output(self, user_id: str, text: str) -> GuardVerdict: ...
