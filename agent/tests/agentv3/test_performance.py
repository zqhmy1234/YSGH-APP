"""Performance smoke: budgets hold under the deterministic fakes.

This is not a load test: it catches accidental extra LLM calls or an unbounded
loop long before a real latency run. Thresholds are the plan's P95 gates.
"""

import statistics
import time
from datetime import datetime

from agentv3.adapters.fakes import FakeClock, FakeEvents, FakeLLM, FakeSearch, _hit
from agentv3.agents.muse import Muse
from agentv3.contracts import Frame, FrameSegment, TurnRequest
from agentv3.memory.ledger import MemoryStore
from agentv3.orchestrator import Orchestrator


def _orch() -> Orchestrator:
    return Orchestrator(
        llm=FakeLLM(["我在听，慢慢说。"] * 200),
        search=FakeSearch([_hit("e1", "西湖")]),
        events=FakeEvents(),
        store=MemoryStore(),
        clock=FakeClock(datetime(2026, 11, 23, 10, 0)),
    )


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]


def test_turn_p95_under_budget_and_llm_calls_capped():
    orch = _orch()
    samples: list[float] = []
    for _ in range(50):
        started = time.perf_counter()
        result = orch.run(TurnRequest(person_id="p1", text="今天有点累"))
        samples.append((time.perf_counter() - started) * 1000)
        assert result.cost.llm_calls <= 2
    assert _p95(samples) <= 5500
    assert statistics.mean(samples) <= 5500


def test_muse_p95_under_budget():
    muse = Muse(FakeLLM(["我在听。"] * 100))
    frame = Frame(segments=(FrameSegment(name="persona", text="陪伴者"),))
    samples: list[float] = []
    for _ in range(50):
        started = time.perf_counter()
        muse.speak(frame, user_text="今天有点累")
        samples.append((time.perf_counter() - started) * 1000)
    assert _p95(samples) <= 4200
