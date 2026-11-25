"""B4 Agent-side wiring: ECHO intent must read backend candidates.

The backend endpoint ``POST /api/v1/echo/agent`` is read-only: it must NOT write
EchoHistory or an in-app message. Only the user-side ``/echo/today`` consumes
the daily slot. These tests prove the adapter contract and the orchestrator path.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from adapters.fakes import FakeClock, FakeEvents, FakeLLM, FakeSearch, _hit  # noqa: E402
from harness.contracts import Intent, TurnRequest  # noqa: E402
from harness.orchestrator import Orchestrator  # noqa: E402
from memory.ledger import MemoryStore  # noqa: E402


class _EchoBackend:
    def __init__(self, items=None, suppressed="", error=False):
        self.items = items or []
        self.suppressed = suppressed
        self.error = error
        self.calls: list[tuple] = []

    def echo(self, person_id, *, local_date, limit=1):
        self.calls.append((person_id, local_date, limit))
        if self.error:
            raise RuntimeError("backend down")
        if self.items:
            item = self.items[0]
            return (
                (
                    {
                        "echo_id": "echo-1",
                        "person_id": person_id,
                        "title": item,
                        "reason": "anniversary",
                        "sensitivity": "personal",
                        "score": 0.8,
                        "why_now": "去年的今天",
                    },
                ),
                "",
            )
        return (), self.suppressed


def _orch(llm, backend):
    orch = Orchestrator(
        llm=llm,
        search=FakeSearch([_hit("e1", "西湖")]),
        events=FakeEvents(),
        store=MemoryStore(),
        clock=FakeClock(datetime(2026, 11, 23, 10, 0)),
    )
    orch.set_backend(backend)
    return orch


def test_echo_intent_reads_backend_candidate():
    """ECHO 意图必须真实调 backend.echo()（接线前 side_effect 无人消费）。"""
    backend = _EchoBackend(items="去年今日的一张照片")
    orch = _orch(FakeLLM(["那天的照片还在。"]), backend)
    req = TurnRequest(person_id="p1", text="去年今天有什么回响")
    result = orch.run(req)
    assert result.plan is not None and result.plan.intent is Intent.ECHO
    assert len(backend.calls) == 1, f"backend.echo 应被调用一次，实际 {backend.calls}"
    assert backend.calls[0][0] == "p1"
    assert backend.calls[0][1] == "2026-11-23", "local_date 用编排时钟而非系统时钟"


def test_echo_no_candidate_does_not_invent():
    """无候选时 backend 回 suppressed，编排不得编造回忆（回复由 FakeLLM 决定，但不得报错）。"""
    backend = _EchoBackend(suppressed="no_candidate")
    orch = _orch(FakeLLM(["今天没什么特别的。"]), backend)
    result = orch.run(TurnRequest(person_id="p1", text="去年今天有什么回响"))
    assert len(backend.calls) == 1
    assert result.reply


def test_echo_backend_failure_degrades_not_crash():
    """backend 不可用是 optional failure：标记 echo 降级，但整轮不崩。"""
    backend = _EchoBackend(error=True)
    orch = _orch(FakeLLM(["我这边暂时想不起来。"]), backend)
    result = orch.run(TurnRequest(person_id="p1", text="去年今天有什么回响"))
    assert len(backend.calls) == 1
    assert result.reply


def test_non_echo_intent_never_calls_echo():
    """非 ECHO 意图不得调 /echo/agent（避免无谓请求与越权读取）。"""
    backend = _EchoBackend(items="不该被读到")
    orch = _orch(FakeLLM(["我在听。"]), backend)
    orch.run(TurnRequest(person_id="p1", text="今天有点累"))
    assert backend.calls == []


def test_backend_http_echo_contract(monkeypatch):
    """适配器把后端 EchoAgentCandidate 正确映射成 harness.EchoCandidate。"""
    from adapters.backend_http import BackendHTTP
    from harness.contracts import EchoCandidate

    adapter = BackendHTTP(base_url="http://x", token="t", user_id="p1")
    payload = {
        "items": [{
            "echo_id": "echo-9", "person_id": "p1", "title": "桂花开了",
            "reason": "anniversary", "sensitivity": "personal",
            "score": 0.8, "why_now": "去年的今天",
        }],
        "suppressed": "",
    }
    monkeypatch.setattr(adapter, "_post", lambda *a, **k: payload)
    candidates, suppressed = adapter.echo("p1", local_date="2026-11-23")
    assert len(candidates) == 1 and suppressed == ""
    assert isinstance(candidates[0], EchoCandidate)
    assert candidates[0].title == "桂花开了" and candidates[0].echo_id == "echo-9"


def test_backend_http_echo_filters_bad_reason():
    """后端返回非法 reason/sensitivity 时回落到安全默认值，不让契约校验炸掉整轮。"""
    from adapters.backend_http import BackendHTTP

    adapter = BackendHTTP(base_url="http://x", token="t", user_id="p1")
    payload = {
        "items": [{
            "echo_id": "e", "person_id": "p1", "title": "x",
            "reason": "bogus", "sensitivity": "bogus", "score": 1.2, "why_now": "y",
        }], "suppressed": "",
    }
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(adapter, "_post", lambda *a, **k: payload)
    try:
        candidates, _ = adapter.echo("p1", local_date="2026-11-23")
        assert candidates[0].reason == "anniversary"
        assert candidates[0].sensitivity == "personal"
    finally:
        monkeypatch.undo()
