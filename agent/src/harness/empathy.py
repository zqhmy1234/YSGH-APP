"""EmpathyChecker: pure function, single taboo source, fail-open by design.

Three axes: name the feeling, acknowledge its meaning, never judge/diagnose.
The checker never calls a model and never rewrites content; it returns a verdict
that the orchestrator either accepts, asks one bounded rewrite for, or rejects.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

# The only taboo list in the product. RoleCard.taboo must be a subset of this
# list; ``tests/harness/test_empathy.py`` fails if a card invents a new phrase.
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
# A tiny set of *patterns* rather than exact phrases: defining the user's
# emotion is a shape, not a vocabulary item.
_JUDGE_PATTERNS = ("你就是", "你其实", "你一定是", "你不该", "没什么大不了", "想开点")
_DIAGNOSIS = ("抑郁症", "焦虑症", "确诊", "精神病", "人格障碍")


class EmpathyViolation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    quote: str
    axis: Literal["name", "acknowledge", "judge"]


class EmpathyVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    status: Literal["pass", "needs_rewrite", "reject"] = "pass"
    violations: tuple[EmpathyViolation, ...] = ()
    suggested_fix: str | None = None


def _sentences(text: str) -> list[str]:
    import re

    return [part.strip() for part in re.split(r"[。！？!?\n]", text) if part.strip()]


def empathy_violations(
    user_text: str,
    reply_text: str,
    *,
    taboo: Sequence[str] = GLOBAL_TABOO,
    private_without_citation: bool = False,
    crisis: bool = False,
    minor: bool = False,
) -> EmpathyVerdict:
    """Check one reply. Fail-open for normal chat; fail-closed for three classes."""
    if private_without_citation or crisis or minor:
        # Fail-closed classes: any violation (or an empty reply) rejects.
        violations = _scan(reply_text, taboo)
        if violations or not reply_text.strip():
            return EmpathyVerdict(status="reject", violations=tuple(violations), suggested_fix=None)
        return EmpathyVerdict(status="pass")

    violations = _scan(reply_text, taboo)
    if not violations:
        return EmpathyVerdict(status="pass")
    # Normal chat: one bounded rewrite request, never a hard refusal.
    return EmpathyVerdict(
        status="needs_rewrite",
        violations=tuple(violations),
        suggested_fix="把评判或诊断改成对用户感受的复述，并承认这件事对他有意义。",
    )


def _scan(reply_text: str, taboo: Sequence[str]) -> list[EmpathyViolation]:
    out: list[EmpathyViolation] = []
    for sentence in _sentences(reply_text):
        for phrase in taboo:
            if phrase and phrase in sentence:
                out.append(EmpathyViolation(code=f"taboo:{phrase}", quote=sentence, axis="judge"))
        for phrase in _JUDGE_PATTERNS:
            if phrase in sentence:
                out.append(EmpathyViolation(code=f"judge:{phrase}", quote=sentence, axis="judge"))
        for phrase in _DIAGNOSIS:
            if phrase in sentence:
                out.append(EmpathyViolation(code=f"diagnosis:{phrase}", quote=sentence, axis="judge"))
    return out


def directive_for(
    *,
    warmth: int = 3,
    verbosity: int = 2,
    advice: int = 1,
    tone: str = "gentle",
    emotion_display: str = "gentle",
) -> str:
    """Render the 5-dim persona into ≤100 chars for context segment #1b."""
    advice_text = {0: "不主动给建议", 1: "先问再给建议", 2: "可直接给建议"}.get(advice, "先问再给建议")
    return f"温度 {warmth}/5，句长 {verbosity}/5，{advice_text}，语气 {tone}，情绪展示 {emotion_display}"


__all__ = ["GLOBAL_TABOO", "EmpathyVerdict", "EmpathyViolation", "directive_for", "empathy_violations"]
