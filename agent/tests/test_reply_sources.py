"""引用回溯 `sources`（2026-10-17）的契约测试。

钉死的行为：
  1. 来源只从 `save_memory` / `search_memories` 的 **ToolMessage 结构化结果**取
     （不解析回复文本、不猜）；
  2. 条目形状 `{"content_id", "kind", "origin"}`，client 只取 `content_id` 数数量；
  3. 两套 id 空间用 `origin` 区分（app=App contents UUID / agent=agent 自增 id），
     镜像降级时 save_memory 退回 `memory_id`；
  4. 失败/非 JSON 的工具结果不产来源（宁缺勿假）；同一 id 去重；
  5. 无工具调用 ⇒ `sources == []`（backend 缺省空数组，行为不变）。

不建网络、不建库：直接喂消息列表给 `build_reply_meta`（纯函数）。
"""
from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from utils.output_validator import build_reply_meta


def _round_with_tools(tool_results: list[tuple[str, str, dict]]) -> list:
    """构造「本轮」消息：human 入口 + 带 tool_calls 的 AI + 各 ToolMessage。

    tool_results: (tool_name, tool_call_id, result_dict)
    """
    tool_calls = [
        {"name": name, "args": {}, "id": cid, "type": "tool_call"}
        for name, cid, _ in tool_results
    ]
    messages: list = [HumanMessage(content="用户的问题")]
    if tool_calls:
        messages.append(AIMessage(content="", tool_calls=tool_calls))
    for name, cid, result in tool_results:
        messages.append(
            ToolMessage(
                content=json.dumps(result, ensure_ascii=False),
                tool_call_id=cid,
                name=name,
            )
        )
    return messages


def test_save_memory_with_app_content_id():
    """镜像成功：source 用 App UUID，origin=app。"""
    messages = _round_with_tools([
        ("save_memory", "c1", {
            "success": True,
            "memory_id": 7,
            "content_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        }),
    ])
    meta = build_reply_meta(messages)
    assert meta["sources"] == [{
        "content_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
        "kind": "memory",
        "origin": "app",
    }]


def test_save_memory_mirror_degraded_falls_back_to_memory_id():
    """镜像降级（content_id=null）：退回 memory_id，origin=agent——两套空间不混认。"""
    messages = _round_with_tools([
        ("save_memory", "c1", {
            "success": True,
            "memory_id": 7,
            "content_id": None,
        }),
    ])
    meta = build_reply_meta(messages)
    assert meta["sources"] == [{"content_id": "7", "kind": "memory", "origin": "agent"}]


def test_save_memory_duplicate_counts_as_source():
    """duplicate=True 也是真实引用（回复会说"已经记过了"）⇒ 计入来源。"""
    messages = _round_with_tools([
        ("save_memory", "c1", {
            "success": True,
            "duplicate": True,
            "memory_id": 42,
        }),
    ])
    meta = build_reply_meta(messages)
    assert meta["sources"] == [{"content_id": "42", "kind": "memory", "origin": "agent"}]


def test_failed_save_produces_no_source():
    """保存失败 ⇒ 该行不存在，不能当来源（宁缺勿假）。"""
    messages = _round_with_tools([
        ("save_memory", "c1", {"success": False, "message": "保存失败"}),
    ])
    assert build_reply_meta(messages)["sources"] == []


def test_search_memories_merged_sources_and_dedup():
    """search 本地命中（agent id）与 app_contents 命中（UUID）都收；与 save 同 id 去重。"""
    messages = _round_with_tools([
        ("save_memory", "c1", {"success": True, "memory_id": 7, "content_id": None}),
        ("search_memories", "c2", {
            "success": True,
            "memories": [
                {"id": 7, "content": "本地记忆正文不许进 sources"},
                {"id": 99, "content": "另一条本地"},
                {"id": "uuid-aaa", "source": "app_contents", "content": "App 图文"},
                {"id": "uuid-aaa", "source": "app_contents", "content": "重复命中"},
                "not-a-dict",
            ],
        }),
    ])
    sources = build_reply_meta(messages)["sources"]
    assert sources == [
        {"content_id": "7", "kind": "memory", "origin": "agent"},
        {"content_id": "99", "kind": "memory", "origin": "agent"},
        {"content_id": "uuid-aaa", "kind": "memory", "origin": "app"},
    ]
    # 隐私红线：条目里绝不带记忆正文
    assert all("content" not in s for s in sources)


def test_no_tool_calls_empty_sources():
    """无工具调用 ⇒ 空数组（backend 缺省空数组，客户端 N=0 不渲染）。"""
    meta = build_reply_meta([HumanMessage(content="你好"), AIMessage(content="你好呀")])
    assert meta["sources"] == []


def test_non_json_tool_content_ignored():
    """异常兜底的非 JSON 工具文案 ⇒ 没有结构化真相，跳过不炸。"""
    messages = [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[{
            "name": "save_memory", "args": {}, "id": "c1", "type": "tool_call",
        }]),
        ToolMessage(content="操作遇到问题（PermissionError）。", tool_call_id="c1", name="save_memory"),
    ]
    assert build_reply_meta(messages)["sources"] == []
