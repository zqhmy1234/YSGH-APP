"""Family domain: role-scoped visibility over the same memory.

Family is a permission lens, not a separate memory store: the episode stays one
row, and what changes is who may read / export / delete it.
"""

from __future__ import annotations

from collections.abc import Iterable

from harness.contracts import Episode, FamilyAction, FamilyRole, PermissionDecision

from .trust import TrustGate


class FamilyPolicy:
    def __init__(self, gate: TrustGate | None = None) -> None:
        self._gate = gate or TrustGate()

    def can(self, role: FamilyRole, action: FamilyAction, sensitivity) -> PermissionDecision:
        return self._gate.check(role, sensitivity, action)

    def visible(self, role: FamilyRole, episodes: Iterable[Episode]) -> list[Episode]:
        return [e for e in episodes if self._gate.visible(role, e.sensitivity)]

    def describe(self, role: FamilyRole) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for sensitivity, actions in self._gate._ALLOW.get(role, {}).items():
            if actions:
                out[sensitivity.value] = sorted(a.value for a in actions)
        return out
