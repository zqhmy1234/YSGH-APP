"""``plan()``: a pure function from TurnContext to TurnPlan.

Six primary intents plus MIXED, with explicit compatibility mapping for the old
v3 enum. No clock reads, no network, no model: replaying a turn yields the same
plan and trace.
"""

from __future__ import annotations

from harness.contracts import (
    AgentInvocation,
    AgentName,
    Budget,
    Intent,
    TurnContext,
    TurnPlan,
)
from harness.registry import SPECS

# Rule tables are intentionally small and deterministic; an optional small-model
# classifier may only run *after* these rules return no dominant intent and its
# result can never override a crisis or trust rule.
_COMPANION_WORDS = (
    "难过",
    "累",
    "焦虑",
    "想哭",
    "崩",
    "睡不着",
    "烦",
    "孤独",
    "委屈",
    "害怕",
    "空",
    "今天",
    "聊聊",
    "陪我",
    "想你",
    "开心",
    "高兴",
    "谢谢你",
)
_RECALL_WORDS = (
    "记得",
    "上次",
    "之前",
    "那次",
    "哪天",
    "什么时候",
    "照片",
    "那年",
    "那时候",
    "回响",
    "去年",
    "聊过",
    "找一下",
    "多久",
    "后来",
    "说过",
)
_INGEST_WORDS = ("记一下", "帮我记", "记下来", "保存", "别忘", "存一下", "记着", "收藏")
_TRUST_WORDS = (
    "设置",
    "关掉",
    "打开",
    "别推送",
    "雷区",
    "不要提",
    "安静",
    "提醒时间",
    "屏蔽",
    "删除",
    "导出",
    "授权",
    "同意",
    "角色",
    "换一个",
    "隐私",
    "关掉",
    "恢复",
)
_ECHO_WORDS = ("回响", "去年今天", "推给我", "提醒我看", "今天有什么回忆", "给我看看那天")
_OUT_WORDS = ("代码", "sql", "写个程序", "翻译", "天气", "股票", "汇率", "python", "报错", "算一下")


def classify_intent(text: str) -> Intent:
    t = text.strip().lower()
    # TRUST normally wins: settings/authorization text often contains
    # "打开/关闭" plus recall nouns. But if a strong second capability is
    # explicitly requested in the same sentence, the turn is MIXED so the plan
    # can disclose both capabilities instead of silently dropping one.
    trust_hit = any(w in t for w in _TRUST_WORDS)
    if trust_hit:
        strong_recall = any(w in t for w in ("找一下", "照片", "回响", "上次", "去年", "那天", "什么时候"))
        has_ingest = any(w in t for w in _INGEST_WORDS)
        if has_ingest or strong_recall:
            return Intent.MIXED
        return Intent.TRUST
    if any(w in t for w in _OUT_WORDS):
        return Intent.OUT_OF_SCOPE
    hits = {
        Intent.INGEST: any(w in t for w in _INGEST_WORDS),
        Intent.ECHO: any(w in t for w in _ECHO_WORDS),
        Intent.RECALL: any(w in t for w in _RECALL_WORDS),
        Intent.COMPANION: any(w in t for w in _COMPANION_WORDS),
    }
    # ECHO is a strict subset of recall vocabulary ("去年今天"/"回响"): if any
    # ECHO word is present, it wins over RECALL. Otherwise "去年今天有什么回响"
    # would be routed to the Librarian and never reach the echo planner.
    if hits[Intent.ECHO]:
        return Intent.ECHO
    return _pick(hits, t)


def _pick(hits: dict[Intent, bool], text: str) -> Intent:
    active = [intent for intent, on in hits.items() if on]
    if not active:
        return Intent.COMPANION
    if len(active) >= 2:
        # Two capabilities in one sentence is MIXED unless one capability is
        # only an incidental word ("记一下" with no evidence is INGEST+COMPANION
        # but the write is the user's actual ask).
        strong = [intent for intent in active if intent is not Intent.COMPANION]
        if len(strong) >= 2:
            return Intent.MIXED
        return strong[0] if strong else Intent.COMPANION
    return active[0]


def _inv(name: AgentName, reason: str, budget_ms: int, budget_chars: int, required: bool = True) -> AgentInvocation:
    return AgentInvocation(name=name, reason=reason, budget_ms=budget_ms, budget_chars=budget_chars, required=required)


def plan(ctx: TurnContext) -> TurnPlan:
    from harness.crisis import scan_local

    if scan_local(ctx.request.text).level == "t3":
        return TurnPlan(
            intent=Intent.OUT_OF_SCOPE,
            invocations=(),
            guards=("policy", "crisis", "handoff"),
            budget=Budget(llm_calls=0, ms=500, chars=500),
            write_policy="none",
            fallback="handoff",
            response_shape="handoff",
        )
    intent = classify_intent(ctx.request.text)
    degraded = set(ctx.degraded)
    search_down = "search" in degraded or "event" in degraded
    llm_down = "llm" in degraded

    invocations: list[AgentInvocation] = []
    guards = ["policy", "crisis", "guard"]
    side_effects: list[tuple[str, str]] = []
    evidence_policy = "none"
    sub_intent = ""
    response_shape = "reply"
    write_policy = "none"

    if intent is Intent.COMPANION:
        sub_intent = "emotion" if any(w in ctx.request.text for w in _COMPANION_WORDS) else "chat"
        invocations.append(_inv(AgentName.MUSE, "only speaker", SPECS[AgentName.MUSE].max_ms, 2200))
    elif intent is Intent.RECALL:
        sub_intent = "search"
        evidence_policy = "required"
        if not search_down:
            invocations.append(
                _inv(AgentName.LIBRARIAN, "evidence for recall", SPECS[AgentName.LIBRARIAN].max_ms, 1600)
            )
        invocations.append(_inv(AgentName.MUSE, "only speaker", SPECS[AgentName.MUSE].max_ms, 2200))
        invocations.append(
            _inv(AgentName.PROVENANCE, "private/numeric claims need evidence", SPECS[AgentName.PROVENANCE].max_ms, 800)
        )
        response_shape = "clarify" if evidence_policy == "required" else "reply"
    elif intent is Intent.INGEST:
        sub_intent = "capture"
        write_policy = "propose"
        invocations.append(_inv(AgentName.MUSE, "acknowledge capture", SPECS[AgentName.MUSE].max_ms, 2200))
        invocations.append(_inv(AgentName.SCULPTOR, "propose memory entry", SPECS[AgentName.SCULPTOR].max_ms, 1000))
        response_shape = "receipt"
    elif intent is Intent.ECHO:
        sub_intent = "anniversary"
        evidence_policy = "optional"
        side_effects.append(("schedule_echo", "echo"))
        invocations.append(
            _inv(AgentName.SERENDIPITY, "read-only echo candidates", SPECS[AgentName.SERENDIPITY].max_ms, 1000)
        )
        invocations.append(
            _inv(AgentName.MUSE, "speak only when a candidate exists", SPECS[AgentName.MUSE].max_ms, 2200)
        )
        response_shape = "receipt"
    elif intent is Intent.TRUST:
        sub_intent = "setting"
        side_effects.append(("audit", "trust"))
        response_shape = "receipt"
        # TRUST is zero-LLM by design. Only the TrustService may act.
    elif intent is Intent.MIXED:
        sub_intent = "multi"
        evidence_policy = "optional"
        invocations.extend(
            [
                _inv(AgentName.LIBRARIAN, "evidence for mixed recall", SPECS[AgentName.LIBRARIAN].max_ms, 1200),
                _inv(AgentName.MUSE, "only speaker", SPECS[AgentName.MUSE].max_ms, 2200),
                _inv(AgentName.PROVENANCE, "verify private claims", SPECS[AgentName.PROVENANCE].max_ms, 800),
                _inv(AgentName.SCULPTOR, "propose optional write", SPECS[AgentName.SCULPTOR].max_ms, 1000),
            ]
        )
        write_policy = "propose"
    else:
        response_shape = "refusal"

    if search_down:
        invocations = [inv for inv in invocations if inv.name is not AgentName.LIBRARIAN]
        evidence_policy = "optional" if intent is not Intent.RECALL else "required"
    llm_calls = 0 if intent in (Intent.TRUST, Intent.OUT_OF_SCOPE) else (2 if intent is Intent.MIXED else 1)
    if llm_down:
        llm_calls = 0

    budget = Budget(llm_calls=llm_calls, ms=5500, chars=4200)
    if llm_down:
        budget = Budget(llm_calls=0, ms=2000, chars=1200)

    if intent is Intent.OUT_OF_SCOPE:
        fallback = "refuse"
    elif llm_down or (search_down and intent is Intent.RECALL):
        fallback = "degraded_reply"
        if search_down and intent is Intent.RECALL:
            fallback = "clarify"
    elif intent is Intent.RECALL and search_down:
        fallback = "clarify"
    else:
        fallback = "degraded_reply"
    if intent is Intent.OUT_OF_SCOPE:
        response_shape = "refusal"

    return TurnPlan(
        intent=intent,
        sub_intent=sub_intent,
        invocations=tuple(invocations),
        parallel_groups=((AgentName.LIBRARIAN,),) if intent is Intent.RECALL and not search_down else (),
        guards=tuple(guards),
        budget=budget,
        write_policy=write_policy,
        evidence_policy=evidence_policy,
        side_effects=tuple({"kind": k, "idempotency_key": v} for k, v in side_effects),  # type: ignore[arg-type]
        fallback=fallback,
        response_shape=response_shape,
    )
