"""Crisis protocol (agent side): detect, never negotiate, never claim contact.

T3 is *not* handled here in production: per plan §12.4 / 45 §5.6, T3 is short-
circuited in backend L3 before the agent is called, so the agent package must
not contain the hotline number or a handoff implementation. This module only
exposes a conservative local screen for tests and for non-production paths; the
word list and reply are deliberately minimal and contain no resource numbers.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

CrisisLevel = Literal["none", "t1", "t2", "t3"]

# Conservative markers for local screening only. Adding resource numbers or a
# handoff implementation here is forbidden by G8; the L3 short-circuit owns it.
_MARKERS_T3 = ("不想活", "活不下去", "自杀", "结束生命", "自残", "割腕", "跳楼")
_MARKERS_T2 = ("撑不住", "没有意义", "消失算了")


class CrisisSignal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    level: CrisisLevel = "none"
    reason: str = ""


def scan_local(text: str) -> CrisisSignal:
    for marker in _MARKERS_T3:
        if marker in text:
            return CrisisSignal(level="t3", reason=f"marker:{marker}")
    for marker in _MARKERS_T2:
        if marker in text:
            return CrisisSignal(level="t2", reason=f"marker:{marker}")
    return CrisisSignal()


__all__ = ["CrisisLevel", "CrisisSignal", "scan_local"]
