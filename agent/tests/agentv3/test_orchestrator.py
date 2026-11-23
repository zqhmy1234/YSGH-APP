"""Orchestrator: one turn end to end, with every failure branch exercised."""

from datetime import datetime

from adapters.fakes import FakeClock, FakeEvents, FakeLLM, FakeSearch, _hit
from harness.contracts import ResultStatus, TurnRequest
from harness.orchestrator import DEGRADED_REPLY, Orchestrator
from memory.ledger import MemoryStore


def _orch(llm=None, search=None, events=None, store=None):
    store = store or MemoryStore()
    return Orchestrator(
        llm=llm or FakeLLM(["我在听，慢慢说。"]),
        search=search or FakeSearch([_hit("e1", "西湖")]),
        events=events or FakeEvents(),
        store=store,
        clock=FakeClock(datetime(2026, 11, 23, 10, 0)),
    )


def test_chitchat_turn_is_grounded_and_uses_one_call():
    llm = FakeLLM(["我在听，慢慢说。"])
    result = _orch(llm=llm).run(TurnRequest(person_id="p1", text="今天有点累"))
    assert result.status is ResultStatus.GROUNDED
    assert result.reply and result.cost.llm_calls == 1
    assert result.plan is not None


def test_recall_turn_carries_citations_from_evidence():
    result = _orch(llm=FakeLLM(["上次是去西湖 [S1]。"])).run(TurnRequest(person_id="p1", text="上次去哪了"))
    assert result.cites == ("S1",)
    assert result.trace


def test_uncited_private_claim_downgrades_to_partial():
    result = _orch(llm=FakeLLM(["她去年住在城西。"])).run(TurnRequest(person_id="p1", text="上次她住哪"))
    assert result.status is ResultStatus.PARTIAL


def test_crisis_short_circuits_without_llm_call():
    llm = FakeLLM(["不应被调用"])
    result = _orch(llm=llm).run(TurnRequest(person_id="p1", text="我不想活了"))
    assert not llm.calls
    assert "急救" in result.reply


def test_out_of_scope_refuses_without_llm():
    llm = FakeLLM(["不应被调用"])
    result = _orch(llm=llm).run(TurnRequest(person_id="p1", text="帮我写个 python 脚本"))
    assert not llm.calls and "不在" in result.reply


def test_llm_down_returns_degraded_reply_and_partial_status():
    result = _orch(llm=FakeLLM(fail=True)).run(TurnRequest(person_id="p1", text="今天有点累"))
    assert result.reply == DEGRADED_REPLY
    assert result.degraded and result.status is ResultStatus.PARTIAL
    assert "llm" in result.degraded_ports


def test_remember_turn_proposes_a_draft_only():
    store = MemoryStore()
    result = _orch(store=store).run(TurnRequest(person_id="p1", text="帮我记一下今天她来了"))
    assert result.receipts and result.receipts[0].state.value == "draft"
    assert store.entries("p1") == []


def test_out_of_scope_never_writes():
    store = MemoryStore()
    _orch(store=store).run(TurnRequest(person_id="p1", text="帮我记一下 sql 怎么写"))
    assert store.entries("p1") == []


def test_declared_llm_outage_skips_model_call():
    llm = FakeLLM(["不应被调用"])
    result = _orch(llm=llm).run(TurnRequest(person_id="p1", text="今天有点累"), degraded=("llm",))
    assert not llm.calls
    assert result.reply == DEGRADED_REPLY
    assert result.status is ResultStatus.PARTIAL
    assert result.cost.llm_calls == 0
