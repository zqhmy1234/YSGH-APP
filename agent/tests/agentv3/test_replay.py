"""Replay: 200 scripted turns, same plan/status/write policy every run."""

import json
from datetime import datetime
from pathlib import Path

from adapters.fakes import FakeClock, FakeEvents, FakeLLM, FakeSearch, _hit
from harness.contracts import TurnRequest
from harness.orchestrator import Orchestrator
from memory.ledger import MemoryStore

GOLDEN = Path(__file__).with_name("golden") / "replay_200.json"

CASES = [
    ("今天有点累", "emotion", "grounded", "none"),
    ("上次去杭州是哪天", "recall", "grounded", "none"),
    ("帮我记一下今天她来了", "remember", "grounded", "propose"),
    ("以后别推送了", "setting", "grounded", "propose"),
    ("帮我写个 python 脚本", "out_of_scope", "grounded", "none"),
    ("你好呀", "chitchat", "grounded", "none"),
    ("记得去年冬天吗", "recall", "grounded", "none"),
    ("那家店的咖啡", "chitchat", "grounded", "none"),
    ("我想哭", "emotion", "grounded", "none"),
    ("存一下这句话", "remember", "grounded", "propose"),
    ("屏蔽这个词", "setting", "grounded", "propose"),
    ("翻译一下这段", "out_of_scope", "grounded", "none"),
    ("她后来怎么样了", "recall", "grounded", "none"),
    ("找一下去年四月的照片", "recall", "grounded", "none"),
    ("今天天气不错", "out_of_scope", "grounded", "none"),
    ("我睡不着", "emotion", "grounded", "none"),
    ("记着下周三提醒我", "remember", "grounded", "propose"),
    ("把提醒时间改到晚上", "setting", "grounded", "propose"),
    ("汇率是多少", "out_of_scope", "grounded", "none"),
    ("那年夏天在海边", "recall", "grounded", "none"),
]


def _orch() -> Orchestrator:
    return Orchestrator(
        llm=FakeLLM(["我在听，慢慢说。"] * 50),
        search=FakeSearch([_hit("e1", "西湖")]),
        events=FakeEvents(),
        store=MemoryStore(),
        clock=FakeClock(datetime(2026, 11, 23, 10, 0)),
    )


def test_replay_matches_golden():
    orchestrator = _orch()
    observed = []
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert len(golden) == 200
    cases = [(row["text"], row["intent"], row["status"], row["write_policy"]) for row in golden]
    for text, intent, _status, write_policy in cases:
        result = orchestrator.run(TurnRequest(person_id="p1", text=text))
        observed.append(
            {
                "text": text,
                "intent": result.plan.intent.value,
                "status": result.status.value,
                "write_policy": result.plan.write_policy,
                "degraded": result.degraded,
            }
        )
        assert result.plan.intent.value == intent
        assert result.plan.write_policy == write_policy

    assert observed == golden
