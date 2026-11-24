"""Two-phase write state machine, expressed without storage.

The orchestrator may call ``next_state`` to decide what to tell the user; the
infra layer is the only place that persists a receipt.
"""

from __future__ import annotations

from typing import Literal

Phase = Literal["propose", "commit", "purge"]
State = Literal["draft", "committed", "rejected", "conflict", "unavailable"]


def decide(*, phase: Phase, is_correction_conflict: bool, has_evidence: bool, store_ok: bool) -> State:
    if not store_ok:
        return "unavailable"
    if is_correction_conflict:
        return "conflict"
    if phase in ("propose", "commit") and not has_evidence:
        return "rejected"
    if phase == "propose":
        return "draft"
    if phase == "commit":
        return "committed"
    return "committed"


__all__ = ["Phase", "State", "decide"]
