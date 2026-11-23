"""Muse: bounded loop, read-only tools, reply validation gates the turn."""

import pytest

from agentv3.adapters.fakes import FakeLLM, FakeTool
from agentv3.agents.muse import MAX_REPLY_CHARS, MAX_STEPS, Muse
from agentv3.contracts import Frame, FrameSegment, ToolCall
from agentv3.obs import CostMeter
from agentv3.ports import PortError


def _frame() -> Frame:
    return Frame(segments=(FrameSegment(name="persona", text="你是陪伴者。"),))


def test_validator_flags_empty_and_too_long():
    assert "empty" in Muse.validate_reply("")
    assert "too_long" in Muse.validate_reply("字" * (MAX_REPLY_CHARS + 1))


def test_validator_flags_wrong_language_for_zh():
    assert "wrong_language" in Muse.validate_reply("hello there", "zh")


def test_cites_are_deduplicated():
    assert Muse.cites("西湖 [S1] 和 [S2] 还有 [S1]") == ("S1", "S2")


def test_single_call_turn_records_cost():
    meter = CostMeter()
    muse = Muse(FakeLLM(["我在听你说"]))
    reply, cites = muse.speak(_frame(), user_text="今天有点累", meter=meter)
    assert reply == "我在听你说" and cites == ()
    assert meter.llm_calls == 1


def test_tool_call_is_executed_then_answered():
    tool = FakeTool("西湖原文")
    llm = FakeLLM(["按检索结果，是西湖 [S1]。"], tool_calls=(ToolCall(name="search", arguments={"q": "西湖"}),))
    muse = Muse(llm, {"search": tool})
    reply, cites = muse.speak(_frame(), user_text="上次去哪了")
    assert tool.calls == 1 and "西湖" in reply and cites == ("S1",)
    assert len(llm.calls) <= MAX_STEPS


def test_unknown_tool_does_not_crash_the_turn():
    llm = FakeLLM(["我不确定，能再说说吗？"], tool_calls=(ToolCall(name="missing", arguments={}),))
    reply, _ = Muse(llm).speak(_frame(), user_text="上次去哪了")
    assert reply


def test_invalid_reply_raises_and_is_never_returned_as_success():
    muse = Muse(FakeLLM([""]))
    with pytest.raises(PortError):
        muse.speak(_frame(), user_text="hi")


def test_llm_failure_propagates():
    with pytest.raises(PortError):
        Muse(FakeLLM(fail=True)).speak(_frame(), user_text="hi")
