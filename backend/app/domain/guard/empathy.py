"""B7-02 empathy check: three axes, one taboo source, no model self-review.

This is the backend copy of the agent pure function with the same verdict shape.
The human-readable source of truth is ``docs/禁语清单.md``; role cards may only
use a subset of ``GLOBAL_TABOO``. Normal chat fails open on checker exceptions;
private-without-citation / crisis / minor fail closed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

GLOBAL_TABOO: tuple[str, ...] = (
    "别想太多",
    "你为什么这样",
    "怀念吗",
    "你应该",
    "这有什么好难过",
    "你就是难过",
    "你其实很在意",
    "你一定是",
)
_JUDGE_PATTERNS = ("你就是", "你其实", "你一定是", "你不该", "没什么大不了", "想开点")
_DIAGNOSIS = ("抑郁症", "焦虑症", "确诊", "精神病", "人格障碍")


@dataclass(frozen=True)
class EmpathyViolation:
    code: str
    quote: str
    axis: Literal["name", "acknowledge", "judge"] = "judge"


@dataclass(frozen=True)
class EmpathyVerdict:
    status: Literal["pass", "needs_rewrite", "reject"] = "pass"
    violations: tuple[EmpathyViolation, ...] = ()
    suggested_fix: str = ""
    fail_open: bool = False
    details: tuple[str, ...] = field(default_factory=tuple)


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in re.split(r"[。！？!?\n]", text or "") if part.strip()]


def _scan(reply_text: str, taboo: tuple[str, ...]) -> list[EmpathyViolation]:
    out: list[EmpathyViolation] = []
    for sentence in _sentences(reply_text):
        for phrase in taboo:
            if phrase and phrase in sentence:
                out.append(EmpathyViolation(code=f"taboo:{phrase}", quote=sentence))
        for phrase in _JUDGE_PATTERNS:
            if phrase in sentence:
                out.append(EmpathyViolation(code=f"judge:{phrase}", quote=sentence))
        for phrase in _DIAGNOSIS:
            if phrase in sentence:
                out.append(EmpathyViolation(code=f"diagnosis:{phrase}", quote=sentence))
    return out


def check_empathy(
    user_text: str,
    reply_text: str,
    *,
    taboo: tuple[str, ...] = GLOBAL_TABOO,
    private_without_citation: bool = False,
    crisis: bool = False,
    minor: bool = False,
) -> EmpathyVerdict:
    try:
        violations = _scan(reply_text, taboo)
    except Exception as exc:  # noqa: BLE001 —— 普通对话 fail-open；三类 fail-closed
        if private_without_citation or crisis or minor:
            return EmpathyVerdict(status="reject", details=(f"checker_error:{type(exc).__name__}",))
        return EmpathyVerdict(fail_open=True, details=(f"checker_error:{type(exc).__name__}",))

    fail_closed = private_without_citation or crisis or minor
    if fail_closed:
        if violations or not (reply_text or "").strip():
            return EmpathyVerdict(status="reject", violations=tuple(violations))
        return EmpathyVerdict(status="pass")
    if not violations:
        return EmpathyVerdict(status="pass")
    return EmpathyVerdict(
        status="needs_rewrite",
        violations=tuple(violations),
        suggested_fix="把评判或诊断改成对用户感受的复述，并承认这件事对他有意义。",
    )


__all__ = ["GLOBAL_TABOO", "EmpathyVerdict", "EmpathyViolation", "check_empathy"]
