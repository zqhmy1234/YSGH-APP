"""``plan()``: a pure function from TurnContext to TurnPlan.

No clock reads, no network, no model: the table below is the whole decision
logic, so a replay of any turn yields the same plan.
"""

from __future__ import annotations

from .contracts import (
    AgentInvocation,
    AgentName,
    Budget,
    Intent,
    TurnContext,
    TurnPlan,
)
from .registry import SPECS

_RECALL_WORDS = (
    "记得", "上次", "之前", "那次", "哪天", "什么时候", "照片", "那年",
    "那时候", "回响", "去年", "聊过", "找一下", "多久", "后来",
)
_REMEMBER_WORDS = ("记一下", "帮我记", "记下来", "保存", "别忘", "存一下", "记着")
_EMOTION_WORDS = ("难过", "累", "焦虑", "想哭", "崩", "睡不着", "烦", "孤独", "委屈", "害怕", "空")
_SETTING_WORDS = ("设置", "关掉", "打开", "别推送", "雷区", "不要提", "安静", "提醒时间", "屏蔽")
_OUT_WORDS = ("代码", "sql", "写个程序", "翻译", "天气", "股票", "汇率", "python", "报错", "算一下")


def classify_intent(text: str) -> Intent:
    """Rule table, longest-first so "帮我记一下" never lands in recall."""
    t = text.strip().lower()
    if any(w in t for w in _SETTING_WORDS):
        return Intent.SETTING
    if any(w in t for w in _REMEMBER_WORDS):
        return Intent.REMEMBER
    if any(w in t for w in _OUT_WORDS):
        return Intent.OUT_OF_SCOPE
    if any(w in t for w in _RECALL_WORDS):
        return Intent.RECALL
    if any(w in t for w in _EMOTION_WORDS):
        return Intent.EMOTION
    return Intent.CHITCHAT


def _inv(name: AgentName, reason: str, budget_ms: int, budget_chars: int, required: bool = True) -> AgentInvocation:
    return AgentInvocation(
        name=name, reason=reason, budget_ms=budget_ms, budget_chars=budget_chars, required=required
    )


def plan(ctx: TurnContext) -> TurnPlan:
    intent = classify_intent(ctx.request.text)
    degraded = set(ctx.degraded)

    # Degraded search can never answer a recall; announcing a clarify would be a lie.
    search_down = "search" in degraded or "event" in degraded
    llm_down = "llm" in degraded

    invocations: list[AgentInvocation] = []
    guards = ["policy", "crisis"]

    if intent in (Intent.RECALL, Intent.REMEMBER) and not search_down:
        invocations.append(_inv(AgentName.LIBRARIAN, "evidence for recall", SPECS[AgentName.LIBRARIAN].max_ms, 1600))

    invocations.append(_inv(AgentName.MUSE, "only speaker", SPECS[AgentName.MUSE].max_ms, 2200))

    if intent in (Intent.RECALL, Intent.REMEMBER):
        invocations.append(
            _inv(AgentName.PROVENANCE, "private/numeric claims need evidence", SPECS[AgentName.PROVENANCE].max_ms, 800)
        )
    if intent in (Intent.REMEMBER, Intent.SETTING):
        invocations.append(
            _inv(AgentName.SCULPTOR, "propose memory entry", SPECS[AgentName.SCULPTOR].max_ms, 1000)
        )

    if intent == Intent.OUT_OF_SCOPE:
        fallback = "refuse"
    elif search_down or llm_down:
        fallback = "degraded_reply"
    elif intent == Intent.RECALL and not invocations:
        fallback = "clarify"
    else:
        fallback = "degraded_reply"

    write_policy = "propose" if intent in (Intent.REMEMBER, Intent.SETTING) else "none"

    budget = Budget(
        llm_calls=2 if intent in (Intent.REMEMBER, Intent.SETTING) else 1,
        ms=5500,
        chars=4200,
    )
    if llm_down:
        budget = Budget(llm_calls=0, ms=2000, chars=1200)

    return TurnPlan(
        intent=intent,
        invocations=tuple(invocations),
        guards=tuple(guards),
        budget=budget,
        write_policy=write_policy,
        fallback=fallback,
    )
