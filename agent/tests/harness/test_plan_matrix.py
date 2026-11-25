"""L4 acceptance matrix: 6 intents × 3 variants, MIXED 2/3 capability edges.

This is the plan §6.7 "18 plan 单测" row, expressed as a table. Each row checks
intent, write policy, LLM budget and whether the Librarian may run.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from harness.contracts import AgentName, Intent, TurnContext, TurnRequest
from harness.plan import plan


def _plan(text: str, degraded: tuple[str, ...] = ()):
    return plan(
        TurnContext(
            request=TurnRequest(person_id="p1", text=text), now=datetime(2026, 11, 23, 10, 0), degraded=degraded
        )
    )


CASES = [
    # COMPANION × 3
    ("今天有点累", Intent.COMPANION, "none", 1),
    ("陪我聊聊", Intent.COMPANION, "none", 1),
    ("你好呀", Intent.COMPANION, "none", 1),
    # RECALL × 3
    ("上次去杭州是哪天", Intent.RECALL, "none", 1),
    ("记得去年冬天吗", Intent.RECALL, "none", 1),
    ("找一下去年四月的照片", Intent.RECALL, "none", 1),
    # INGEST × 3
    ("帮我记一下她喜欢桂花", Intent.INGEST, "propose", 1),
    ("保存这句话", Intent.INGEST, "propose", 1),
    ("记着下周三提醒我", Intent.INGEST, "propose", 1),
    # ECHO × 3
    ("去年今天有什么回响", Intent.ECHO, "none", 1),
    ("给我看看那天的回忆", Intent.ECHO, "none", 1),
    ("推给我去年今天的照片", Intent.ECHO, "none", 1),
    # TRUST × 3
    ("以后别推送了", Intent.TRUST, "none", 0),
    ("把提醒时间改到晚上", Intent.TRUST, "none", 0),
    ("删除我之前那条记录", Intent.TRUST, "none", 0),
    # MIXED × 2 (2-capability and 3-capability)
    ("帮我记一下上次去杭州吃了什么", Intent.MIXED, "propose", 2),
    ("记住这个设置：以后别推送，并找一下去年四月的照片", Intent.MIXED, "propose", 2),
    # OUT_OF_SCOPE × 1
    ("帮我写个 python 脚本", Intent.OUT_OF_SCOPE, "none", 0),
]


@pytest.mark.parametrize(("text", "intent", "write_policy", "llm_calls"), CASES)
def test_intent_matrix(text, intent, write_policy, llm_calls):
    result = _plan(text)
    assert result.intent is intent, (text, result.intent)
    assert result.write_policy == write_policy
    assert result.budget.llm_calls == llm_calls
    if intent is Intent.TRUST:
        assert result.invocations == ()
    if intent is Intent.OUT_OF_SCOPE:
        assert result.fallback == "refuse"


def test_mixed_over_two_capabilities_is_disclosed_and_budgeted():
    result = _plan("记住这个设置：以后别推送，找一下去年四月的照片，再导出全部记录")
    assert result.intent is Intent.MIXED
    # MIXED never silently grows beyond the two-call budget (plan §6.4).
    assert result.budget.llm_calls <= 2
    assert result.write_policy == "propose"
    assert "sculptor" in {inv.name.value for inv in result.invocations}


def test_search_down_recall_asks_clarify_and_never_calls_librarian():
    result = _plan("上次去杭州是哪天", ("search",))
    assert AgentName.LIBRARIAN not in {inv.name for inv in result.invocations}
    assert result.fallback == "clarify"


def test_llm_down_zero_calls_for_every_intent():
    for text, *_ in CASES:
        assert _plan(text, ("llm",)).budget.llm_calls == 0


def test_crisis_never_reaches_plan_invocations():
    result = _plan("我不想活了")
    assert AgentName.MUSE not in {inv.name for inv in result.invocations}
