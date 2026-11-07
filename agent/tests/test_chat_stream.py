"""`/v1/chat/stream`（SSE）与 `stream_agent_with_validation` 的契约测试。

本文件钉死的是**流式专属**的几条不可退让的行为（同步版的行为由既有测试守着）：
  1. 事件顺序：`final` **最后且仅一条**；`replace` 只可能出现在它之前；
  2. 预览（delta）拼接 == 第 1 轮生成的正文（不是终稿，但必须完整对得上）；
  3. **写库副作用不可重试**：重复 `save_memory` 判定 ⇒ 不重试、不换稿（同同步版口径）；
  4. 校验不过 ⇒ `replace` 整段替换（客户端据此覆盖预览，而不是追加）；
  5. 工具状态事件能出来（UI 的"在翻记忆"靠它）。

替身策略：`_FakeAgent` 只实现流式运行器真正用到的 `stream` / `get_state`，
**不建网络、不建库**；端点测试把 `main.run_agent_stream` 整个换掉（同样不外呼）。
"""
from __future__ import annotations

import types

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from utils.output_validator import stream_agent_with_validation


class _FakeChunk:
    """AIMessageChunk 的最小替身（运行器只读 `content` / `id`）。"""

    def __init__(self, text: str, mid: str = "m1") -> None:
        self.content = text
        self.id = mid


class _FakeAgent:
    """最小替身：`stream()` 产出 `(mode, payload)`；`get_state()` 给出终态消息。"""

    def __init__(self, deltas=None, updates=None, messages=None) -> None:
        self._deltas = list(deltas or [])
        self._updates = list(updates or [])
        self._messages = list(messages or [])
        self.invoke_calls: list[dict] = []

    def stream(self, _input, _config, stream_mode=None):  # noqa: ARG002 —— 签名对齐
        for text in self._deltas:
            yield ("messages", (_FakeChunk(text), {}))
        for update in self._updates:
            yield ("updates", update)

    def get_state(self, _config):  # noqa: ARG002
        return types.SimpleNamespace(values={"messages": self._messages})

    def invoke(self, payload, _config=None):  # noqa: ARG002 —— 重试轮用
        self.invoke_calls.append(payload)
        return {"messages": self._messages}


def _tool_call(name: str, call_id: str, content: str = "") -> dict:
    return {
        "name": name,
        "args": {"content": content} if content else {},
        "id": call_id,
        "type": "tool_call",
    }


def _events(agent, query: str = "看看我的记忆", max_rounds=None):
    return list(stream_agent_with_validation(agent, query, {}, max_rounds))


# ── 1. 正常路径 ────────────────────────────────────────────────────────────────
def test_deltas_then_single_final():
    """delta 拼接 == 第 1 轮正文；工具事件成对出现；final 唯一且在最后。"""
    text = "翻到了三张照片"
    agent = _FakeAgent(
        deltas=["翻", "到了三", "张照片"],
        updates=[
            {"model": {"messages": [AIMessage(content="", tool_calls=[_tool_call("search_memories", "c1")])]}},
            {"tools": {"messages": [ToolMessage(content="ok", tool_call_id="c1", name="search_memories")]}},
        ],
        # 终态消息（get_state）与真实 checkpoint 同构：本轮 human → 工具调用 → 工具结果 → 终稿
        messages=[
            HumanMessage(content="看看我的记忆"),
            AIMessage(content="", tool_calls=[_tool_call("search_memories", "c1")]),
            ToolMessage(content="ok", tool_call_id="c1", name="search_memories"),
            AIMessage(content=text),
        ],
    )
    events = _events(agent)
    kinds = [k for k, _ in events]

    assert kinds[-1] == "final"
    assert kinds.count("final") == 1
    assert "".join(p for k, p in events if k == "delta") == text
    assert [p for k, p in events if k == "tool"] == [
        {"phase": "started", "name": "search_memories"},
        {"phase": "done", "name": "search_memories"},
    ]

    final = events[-1][1]
    assert final["_diag"]["status"] == "passed"
    assert final["_diag"]["streamed"] is True
    # ⚠️ 计数口径沿用同步版 `_count_tool_messages` / `tool_call_distribution`：
    # 一次工具调用会同时被「AIMessage.tool_calls」与「ToolMessage」各记一次 ⇒ 值为 2。
    # 这里**刻意不改**（改了流式/同步两端口径就不一致，跨端点比延迟会失去可比性）。
    assert final["_diag"]["tool_calls"] == 2
    assert final["_diag"]["tool_dist"] == {"search_memories": 2}
    assert final["messages"][-1].content == text


def test_tool_call_only_chunks_are_not_previewed():
    """只带 tool_call、正文为空的分片**不得**进预览（否则会出现空气泡）。"""
    agent = _FakeAgent(
        deltas=["", "好", ""],
        messages=[HumanMessage(content="q"), AIMessage(content="好")],
    )
    events = _events(agent)
    deltas = [p for k, p in events if k == "delta"]
    assert deltas == ["好"]


# ── 2. 校验不过 ⇒ 整段替换 ─────────────────────────────────────────────────────
def test_validation_failure_replaces_preview(monkeypatch):
    """校验不过且可重试 ⇒ 出 `replace`（整段），终态取重试轮，validator_rounds=1。"""
    bad = "hello 我记下了"  # 含英文 ⇒ 命中语言限制（可重试的纯输出形态问题）
    good = "好的，已经帮你记下了"
    agent = _FakeAgent(
        deltas=[bad],
        messages=[HumanMessage(content="记一下"), AIMessage(content=bad)],
    )
    retry_agent = _FakeAgent(
        messages=[
            HumanMessage(content="记一下"),
            AIMessage(content=bad),
            HumanMessage(content="[校验反馈] …"),
            AIMessage(content=good),
        ]
    )
    monkeypatch.setattr("agents.agent.build_agent_no_tools", lambda: retry_agent)

    events = _events(agent, "记一下")
    kinds = [k for k, _ in events]

    assert kinds.count("replace") == 1
    assert kinds.index("replace") < kinds.index("final")
    assert [p for k, p in events if k == "replace"] == [good]
    assert [p for k, p in events if k == "delta"] == [bad]

    final = events[-1][1]
    assert final["_diag"]["status"] == "passed"
    assert final["_diag"]["rounds"] == 2
    assert final["_diag"]["validator_rounds"] == 1
    assert len(retry_agent.invoke_calls) == 1


def test_duplicate_save_memory_is_not_retried():
    """**关键**：重复保存同一内容 ⇒ 终态即终态，不重试（记忆已写入，重跑只会再写一次）。"""
    dup = _tool_call("save_memory", "c1", "同一段记忆")
    dup2 = _tool_call("save_memory", "c2", "同一段记忆")
    agent = _FakeAgent(
        deltas=["已经帮你记下了"],
        messages=[
            HumanMessage(content="记一下"),
            AIMessage(content="", tool_calls=[dup]),
            ToolMessage(content="已保存", tool_call_id="c1", name="save_memory"),
            AIMessage(content="", tool_calls=[dup2]),
            ToolMessage(content="已保存", tool_call_id="c2", name="save_memory"),
            AIMessage(content="已经帮你记下了"),
        ],
    )
    events = _events(agent)
    kinds = [k for k, _ in events]

    assert "replace" not in kinds, "不可重试的判定不得触发换稿"
    assert events[-1][1]["_diag"]["status"] == "terminal:duplicate_tool_call"


# ── 3. 边界 ───────────────────────────────────────────────────────────────────
def test_empty_reply_still_emits_final():
    """空消息（模型没吐正文）⇒ 仍以 `final` 收尾（调用方据此撤占位，而不是挂住）。"""
    agent = _FakeAgent(messages=[])
    events = _events(agent)
    assert [k for k, _ in events] == ["final"]
    assert events[0][1]["_diag"]["status"] == "empty_messages"


def test_rounds_clamped_to_one_disables_retry(monkeypatch):
    """`max_rounds=0` ⇒ 夹紧为 1 ⇒ 不重试（同同步版：配错环境变量不该把请求打成 500）。"""
    bad = "hello 我记下了"
    agent = _FakeAgent(deltas=[bad], messages=[HumanMessage(content="q"), AIMessage(content=bad)])
    called: list[int] = []
    monkeypatch.setattr("agents.agent.build_agent_no_tools", lambda: called.append(1))

    events = _events(agent, "q", max_rounds=0)
    assert "replace" not in [k for k, _ in events]
    assert events[-1][1]["_diag"]["status"] == "max_rounds"
    assert called == []


# ── 3.5 结构化附加（cards/chips）──────────────────────────────────────────────
def _save_pair(call_id: str, content: str, result_json: str) -> list:
    """构造「模型发起 save_memory → 工具返回 JSON 结果」这一对消息（真实链路同构）。"""
    return [
        AIMessage(
            content="",
            tool_calls=[
                {"name": "save_memory", "args": {"content": content}, "id": call_id, "type": "tool_call"}
            ],
        ),
        ToolMessage(content=result_json, tool_call_id=call_id, name="save_memory"),
    ]


def test_meta_cards_from_tool_result():
    """`save_memory` 成功结果 ⇒ 出一张卡（标题=内容摘要、meta=分类·情绪·主题）+ 追问 chips。"""
    result_json = (
        '{"success": true, "memory_id": "m-1", "category": "情绪", "mood": "开心",'
        ' "topics": ["生活"], "message": "记忆已成功保存"}'
    )
    msgs = [HumanMessage(content="记一下")]
    msgs += _save_pair("c1", "今天在湖边散步很开心", result_json)
    msgs.append(AIMessage(content="已经帮你记下了"))
    agent = _FakeAgent(deltas=["已经帮你记下了"], messages=msgs)

    meta = _events(agent)[-1][1]["_meta"]
    assert len(meta["cards"]) == 1
    card = meta["cards"][0]
    assert card["title"] == "今天在湖边散步很开心"
    assert card["meta"] == "情绪 · 开心 · 生活"
    assert card["topics"] == ["生活"]
    assert card["img"] == "", "文字记忆没有缩略图：必须留空串，客户端据此跳过图片"
    assert "memory_id" not in card, "内部标识不得进给客户端的结构化数据"
    assert meta["chips"] == ["看看「生活」相关的记忆", "再帮我记一条"]


def test_meta_title_truncated_for_long_content():
    """超长内容截断成卡片标题（卡片是摘要，完整正文仍在 content 里）。"""
    long_text = "今天" + "很" * 60 + "开心"
    result_json = '{"success": true, "memory_id": "m-2", "category": "事件", "mood": "平静", "topics": []}'
    msgs = [HumanMessage(content="记一下")]
    msgs += _save_pair("c1", long_text, result_json)
    msgs.append(AIMessage(content="记下了"))
    meta = _events(_FakeAgent(messages=msgs))[-1][1]["_meta"]

    title = meta["cards"][0]["title"]
    assert title.endswith("…")
    assert len(title) == 22


def test_meta_skips_duplicate_and_failed_saves():
    """**关键**：duplicate / success=false 的保存**不产卡**（宁可不出卡，也不出假卡）。"""
    dup = '{"success": true, "duplicate": true, "memory_id": "m-3", "message": "已经保存过了"}'
    failed = '{"success": false, "message": "保存失败，未返回数据"}'
    msgs = [HumanMessage(content="记一下")]
    msgs += _save_pair("c1", "同一段记忆", dup)
    msgs += _save_pair("c2", "另一段记忆", failed)
    msgs.append(AIMessage(content="好了"))
    meta = _events(_FakeAgent(messages=msgs))[-1][1]["_meta"]

    assert meta["cards"] == []
    assert meta["chips"] == []


def test_meta_empty_for_plain_chat():
    """普通闲聊（无工具调用）⇒ 无 cards/chips（回复保持 bubble/plain，不被强行结构化）。"""
    msgs = [HumanMessage(content="你好"), AIMessage(content="你好呀")]
    meta = _events(_FakeAgent(messages=msgs))[-1][1]["_meta"]
    assert meta == {"cards": [], "chips": []}


# ── 4. SSE 端点 ───────────────────────────────────────────────────────────────
def test_sse_endpoint_frames(monkeypatch):
    """端点把事件翻成 SSE 帧：status/delta/done 齐全，且 `data:` 是单行 JSON。"""
    import json

    import main
    from fastapi.testclient import TestClient

    monkeypatch.setattr("yishu.security.service_token", lambda: "")

    def _fake_stream(message, user_id="", ctx=None, max_rounds=None):  # noqa: ARG001
        yield ("delta", "你")
        yield ("tool", {"phase": "started", "name": "search_memories"})
        yield ("delta", "好")
        yield ("final", {"messages": [AIMessage(content="你好")], "_diag": {"status": "passed"}})

    monkeypatch.setattr(main, "run_agent_stream", _fake_stream)

    client = TestClient(main.app)
    resp = client.post("/v1/chat/stream", json={"user_id": "u-test", "message": "在吗"})

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert "event: status" in body
    assert "event: tool" in body
    assert "event: done" in body

    frames = [f for f in body.split("\n\n") if f.strip()]
    events: list[tuple[str, dict]] = []
    for frame in frames:
        lines = [ln for ln in frame.splitlines() if ln.strip()]
        event, data = "", ""
        for ln in lines:
            if ln.startswith("event: "):
                event = ln[len("event: "):]
            elif ln.startswith("data: "):
                data = ln[len("data: "):]
        events.append((event, json.loads(data)))

    assert [e for e, _ in events] == ["status", "delta", "tool", "delta", "done"]
    done = [p for e, p in events if e == "done"][0]
    assert done["reply"] == "你好"
    # 预览拼接 == 终稿正文的前缀（此处桩里两者相同）
    assert "".join(p["text"] for e, p in events if e == "delta") == "你好"
