"""A scripted five-turn conversation over one store.

This is a *flow* test, not a real-LLM transcript: it proves the same session can
move from greeting to remember to recall to setting to refusal without losing
state or mixing write policies.
"""

from __future__ import annotations

from datetime import datetime

from adapters.fakes import FakeClock, FakeEvents, FakeLLM, FakeSearch, _hit
from harness.contracts import Intent, ResultStatus, TurnRequest
from harness.orchestrator import Orchestrator
from memory.ledger import MemoryStore

TURNS = [
    ("你好呀", Intent.COMPANION, "none"),
    ("帮我记一下她喜欢桂花", Intent.INGEST, "propose"),
    ("上次去杭州是哪天", Intent.RECALL, "none"),
    ("以后别推送了", Intent.TRUST, "none"),
    ("帮我写个 python 脚本", Intent.OUT_OF_SCOPE, "none"),
]


def test_five_turn_flow_keeps_policy_and_state():
    store = MemoryStore()
    orchestrator = Orchestrator(
        llm=FakeLLM(["我在听。"] * 20),
        search=FakeSearch([_hit("e1", "西湖")]),
        events=FakeEvents(),
        store=store,
        clock=FakeClock(datetime(2026, 11, 23, 10, 0)),
    )
    seen = []
    for text, intent, write_policy in TURNS:
        result = orchestrator.run(TurnRequest(person_id="p1", text=text))
        assert result.plan is not None
        assert result.plan.intent is intent
        assert result.plan.write_policy == write_policy
        assert result.status in {ResultStatus.GROUNDED, ResultStatus.PARTIAL}
        seen.append(result.plan.intent)
    assert seen == [intent for _, intent, _ in TURNS]
    assert store.entries("p1") == []  # no turn writes without a committed batch
