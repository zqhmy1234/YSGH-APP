"""plan(): deterministic intent routing and degraded budgets."""

from datetime import datetime

import pytest

from harness.contracts import AgentName, Intent, TurnContext, TurnRequest
from harness.plan import classify_intent, plan


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("帮我记一下今天和她吃饭", Intent.INGEST),
        ("上次去杭州是哪天", Intent.RECALL),
        ("我今天有点累", Intent.COMPANION),
        ("以后别推送了", Intent.TRUST),
        ("帮我写个 python 脚本", Intent.OUT_OF_SCOPE),
        ("你好呀", Intent.COMPANION),
    ],
)
def test_intent_table(text, expected):
    assert classify_intent(text) is expected


def _ctx(text: str, degraded: tuple[str, ...] = ()) -> TurnContext:
    return TurnContext(
        request=TurnRequest(person_id="p1", text=text), now=datetime(2026, 11, 23, 10, 0), degraded=degraded
    )


def test_recall_invokes_librarian_and_provenance():
    names = {inv.name for inv in plan(_ctx("上次去杭州是哪天")).invocations}
    assert AgentName.LIBRARIAN in names and AgentName.PROVENANCE in names


def test_search_down_never_invokes_librarian():
    names = {inv.name for inv in plan(_ctx("上次去杭州是哪天", ("search",))).invocations}
    assert AgentName.LIBRARIAN not in names


def test_llm_down_zero_budget():
    p = plan(_ctx("今天有点累", ("llm",)))
    assert p.budget.llm_calls == 0
    assert p.fallback == "degraded_reply"


def test_out_of_scope_refuses():
    assert plan(_ctx("帮我写个 python 脚本")).fallback == "refuse"


def test_write_policy_only_for_ingest_and_trust():
    assert plan(_ctx("帮我记一下")).write_policy == "propose"
    assert plan(_ctx("上次去杭州")).write_policy == "none"


def test_remember_still_has_muse_and_sculptor():
    names = {inv.name for inv in plan(_ctx("帮我记一下今天的事")).invocations}
    assert {AgentName.MUSE, AgentName.SCULPTOR} <= names
