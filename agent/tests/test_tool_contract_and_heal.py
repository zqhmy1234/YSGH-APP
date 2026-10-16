"""2026-10-05 三处真实故障的回归测试。

这三条都是**排查成本极高、且不会被 lint/类型检查发现**的坑，各自在 docstring
里写明了成因；这里只锁行为，根因分析见对应源码注释。

1. `coerce_str_list` —— 模型把数组参数传成 JSON 字符串
   （实测导致 save_memory 函数体一行都没执行、单轮重试 5 次、12~25s 后撞递归上限）。
2. `_find_existing_memory` 的三级判重 —— 逐字去重不足以终止「同一条记忆反复保存」。
3. `_salvage_partial_reply` —— 撞递归上限时捞回已有回复，而不是整轮报废。
"""
from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tools.memory_tools import _common_prefix_len, _find_existing_memory, _norm
from tools.tag_rules import coerce_str_list
from utils.output_validator import _salvage_partial_reply


# ── 1. 数组参数的畸形写法归一化 ─────────────────────────────────────
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        ("   ", None),
        (["旅行", "兴趣"], ["旅行", "兴趣"]),
        (["旅行", "  "], ["旅行"]),          # 丢弃空项
        ('["旅行", "兴趣"]', ["旅行", "兴趣"]),          # JSON 数组字符串（LLM 实测写法）
        ('["旅行","兴趣"]', ["旅行", "兴趣"]),
        ("旅行,兴趣", ["旅行", "兴趣"]),               # 裸字符串 + 英文逗号
        ("旅行，兴趣", ["旅行", "兴趣"]),               # 中文逗号
        ("旅行、兴趣", ["旅行", "兴趣"]),               # 顿号
        ("旅行", ["旅行"]),                             # 单值
    ],
)
def test_coerce_str_list_handles_malformed_shapes(raw, expected):
    assert coerce_str_list(raw) == expected


def test_coerce_str_list_survives_broken_json():
    """`[未闭合` 这类坏 JSON 不能抛异常——归一化失败不该让工具整体不可用。"""
    assert coerce_str_list("[未闭合") is not None
    assert coerce_str_list("[未闭合") == ["[未闭合"]


def test_save_memory_accepts_topics_as_json_string():
    """工具签名必须真的放宽成 `str | list[str]`。

    只在 `coerce_str_list` 里解析是不够的：pydantic 会在**进入函数体之前**
    就把 `'["旅行"]'` 判为类型不符并抛错，函数体永远执行不到。
    """
    from tools.memory_tools import save_memory

    schema = save_memory.args_schema.model_json_schema()
    topics_type = schema["properties"]["topics"].get("anyOf", [])
    flat = [t.get("type") for t in topics_type] or [schema["properties"]["topics"].get("type")]
    assert "string" in flat and "array" in flat, f"topics 签名未放宽：{schema['properties']['topics']}"


# ── 2. 三级判重 ─────────────────────────────────────────────────────
def test_norm_strips_all_whitespace():
    assert _norm("今天 去了\n香山") == _norm("今天去了香山")
    assert _norm("今天\t去了\r\n香山") == _norm("今天去了香山")


def test_common_prefix_len_is_capped():
    a = "x" * 200
    assert _common_prefix_len(a, a + "尾巴") == 50  # 上限 DEDUP_PREFIX_LEN


@pytest.fixture()
def mem_client():
    """真实连库（判重是 SQL + Python 双层逻辑，mock 掉就测不到真东西）。"""
    from storage.database.local_client import create_local_client
    from yishu.context import new_context, request_context
    import uuid

    uid = f"test-dedup-{uuid.uuid4().hex[:8]}"
    request_context.set(new_context(method="test", user_id=uid))
    client = create_local_client()
    yield client, uid
    client.table("memories").delete().eq("user_id", uid).execute()


def test_dedup_levels(mem_client):
    client, uid = mem_client
    base = "今天去爬了香山，秋天的红叶特别美"
    inserted = client.table("memories").insert({"content": base, "user_id": uid}).execute().data[0]
    mem_id = inserted["id"]

    # 1) 逐字
    assert _find_existing_memory(client, uid, base) == (mem_id, "exact")
    # 2) 尾部追加（LLM 重试的典型形态：越改越长）
    assert _find_existing_memory(client, uid, base + "，顺便在山顶吃了碗面。") == (mem_id, "prefix")
    # 3) 截短（反方向同样要能命中）
    assert _find_existing_memory(client, uid, base[:15]) == (mem_id, "prefix")
    # 4) 排版噪声
    assert _find_existing_memory(client, uid, "今天去爬了香山，\n秋天的红叶特别美")[1] == "prefix"
    # 5) 真的不同内容 → 不得误伤
    assert _find_existing_memory(client, uid, "明天打算去图书馆借两本计算机方面的书") == (None, "")


def test_dedup_window_can_be_disabled(mem_client, monkeypatch):
    """窗口关掉后只保留逐字判重（误伤风险高的近似判定可被运营关掉）。"""
    client, uid = mem_client
    base = "今天去爬了香山，秋天的红叶特别美"
    client.table("memories").insert({"content": base, "user_id": uid}).execute()
    monkeypatch.setenv("AGENT_DEDUP_WINDOW_MIN", "0")
    assert _find_existing_memory(client, uid, base + "，顺便在山顶吃了碗面。") == (None, "")


def test_dedup_is_isolated_by_user(mem_client):
    """判重必须按 user_id 隔离：别的用户有相同内容不算重复。"""
    client, uid = mem_client
    client.table("memories").insert({"content": "今天去爬了香山，秋天的红叶特别美", "user_id": uid}).execute()
    assert _find_existing_memory(client, "someone-else-9999", "今天去爬了香山，秋天的红叶特别美") == (None, "")


# ── 3. 递归耗尽时捞回部分回复 ───────────────────────────────────────
class _StubAgent:
    """只提供 get_state —— `_salvage_partial_reply` 不碰真实 LLM。"""

    def __init__(self, messages):
        self._messages = messages

    def get_state(self, config):
        class _S:
            values = {"messages": self._messages}

        return _S()


def test_salvage_picks_last_non_empty_ai_message():
    msgs = [
        HumanMessage(content="今天去爬了香山"),
        AIMessage(content=""),
        ToolMessage(content="{}", tool_call_id="c1", name="save_memory"),
        AIMessage(content="已经帮你记下啦～"),
    ]
    text, returned = _salvage_partial_reply(_StubAgent(msgs), {})
    assert text == "已经帮你记下啦～"
    assert returned == msgs


def test_salvage_returns_none_when_no_text_yet():
    """只有工具调用、还没产出文本时不能硬造一个空回复（交还调用方重抛）。"""
    msgs = [
        HumanMessage(content="记一下"),
        AIMessage(content=""),
        ToolMessage(content="{}", tool_call_id="c1", name="save_memory"),
    ]
    assert _salvage_partial_reply(_StubAgent(msgs), {}) == (None, None)


def test_salvage_survives_state_read_failure():
    class _Broken:
        def get_state(self, config):
            raise RuntimeError("checkpoint 读不了")

    assert _salvage_partial_reply(_Broken(), {}) == (None, None)


# ── 4. checkpoint 悬空 tool_calls 清理 ─────────────────────────────
def test_heal_dangling_tool_calls_returns_zero_on_read_failure():
    """读不到状态时不能阻断正常请求（返回 0 而不是抛）。"""
    from agents.agent import heal_dangling_tool_calls

    class _Broken:
        def get_state(self, config):
            raise RuntimeError("boom")

    assert heal_dangling_tool_calls(_Broken(), {"configurable": {"thread_id": "t"}}) == 0


def test_heal_patches_dangling_tool_calls():
    """有 tool_calls 却缺 ToolMessage 时必须补齐（否则同会话永远重放、永远失败）。"""
    from agents.agent import heal_dangling_tool_calls

    answered = ToolMessage(content="{}", tool_call_id="done_1", name="save_memory")
    dangling = AIMessage(
        content="",
        tool_calls=[{"id": "lost_1", "name": "save_memory", "args": {"content": "x"}}],
    )
    patched: dict = {}
    updated: dict = {}

    class _Agent:
        def get_state(self, config):
            class _S:
                values = {"messages": [HumanMessage(content="hi"), answered, dangling]}

            return _S()

        def update_state(self, config, values):
            updated.update(values)

    n = heal_dangling_tool_calls(_Agent(), {"configurable": {"thread_id": "t"}})
    assert n == 1
    assert isinstance(patched, dict)  # 占位，避免未使用告警
    (msg,) = updated["messages"]
    assert msg.tool_call_id == "lost_1"
    assert isinstance(msg.content, str) and msg.content


def test_heal_noop_when_all_tool_calls_answered():
    from agents.agent import heal_dangling_tool_calls

    ok = AIMessage(content="", tool_calls=[{"id": "c1", "name": "save_memory", "args": {}}])
    replied = ToolMessage(content="{}", tool_call_id="c1", name="save_memory")
    updated: dict = {}

    class _Agent:
        def get_state(self, config):
            class _S:
                values = {"messages": [ok, replied]}

            return _S()

        def update_state(self, config, values):
            updated.update(values)

    assert heal_dangling_tool_calls(_Agent(), {"configurable": {"thread_id": "t"}}) == 0
    assert not updated


# ── 5. 工具参数契约：save_memory 真的能落库（不带 LLM）───────────────
def test_save_memory_persists_and_dedups_end_to_end():
    """端到端（无 LLM）：首次写入 → 尾部追加被判重 → 库中仍只有一条。"""
    from tools.memory_tools import save_memory
    from storage.database.local_client import create_local_client
    from yishu.context import new_context, request_context
    import uuid

    uid = f"test-e2e-{uuid.uuid4().hex[:8]}"
    request_context.set(new_context(method="test", user_id=uid))
    text = f"E2E 回归：黄色台灯 {uuid.uuid4().hex[:6]} 号"
    try:
        first = json.loads(str(save_memory.invoke({"content": text, "topics": '["生活", "兴趣"]'})))
        assert first.get("success") is True
        second = json.loads(str(save_memory.invoke({"content": text + "，放在桌角", "topics": '["生活"]'})))
        assert second.get("duplicate") is True
        rows = create_local_client().table("memories").select("id").eq("user_id", uid).execute().data
        assert len(rows) == 1
    finally:
        create_local_client().table("memories").delete().eq("user_id", uid).execute()
