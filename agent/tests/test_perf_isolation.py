"""2026-10-04 性能/隔离修复的回归测试。

锁住四件事（每一条都对应一个真实故障，改回去必须有测试变红）：

1. **隔离 fail-closed**：`_get_user_id()` 在上下文缺失时**抛错**，
   而不是回落到共享的 `"default"`（那会把所有用户的数据混进同一身份）。
2. **工具层幂等**：`save_memory` 对同用户同内容重复调用不重复写库，
   并明确告知「已保存」——这是终止「反复调用同一工具」循环的关键。
3. **DB 取值可 JSON 序列化**：`created_at` 等 datetime 在**数据出口**
   就转成字符串，工具的 json.dumps 不会再抛
   `Object of type datetime is not JSON serializable`。
4. **PostgREST 语义补齐**：`ilike` 用 `*` 通配并转义用户输入里的 % / _。
"""
from __future__ import annotations

import importlib

import pytest

from storage.database.local_client import _db_value, _pg_pattern
from yishu.context import new_context, request_context

TOOL_MODULES = ["tools.memory_tools", "tools.url_fetch_tools"]


# ── 1. 隔离 fail-closed ─────────────────────────────────────────────
@pytest.mark.parametrize("module_name", TOOL_MODULES)
def test_get_user_id_fails_closed_without_context(module_name):
    """无 user_id 时必须抛错，**绝不能**返回共享身份。"""
    mod = importlib.import_module(module_name)
    request_context.set(new_context(method="test", user_id=None))
    with pytest.raises(PermissionError):
        mod._get_user_id()  # noqa: SLF001


@pytest.mark.parametrize("module_name", TOOL_MODULES)
def test_get_user_id_returns_context_value(module_name):
    """有上下文时正常返回（fail-closed 不能误伤正常路径）。"""
    mod = importlib.import_module(module_name)
    request_context.set(new_context(method="test", user_id="user-42"))
    assert mod._get_user_id() == "user-42"  # noqa: SLF001


def test_run_agent_rejects_empty_user_id():
    """run_agent 入口拒绝空 user_id（原签名默认 "default"）。"""
    from agents.agent import run_agent

    with pytest.raises(ValueError, match="user_id"):
        run_agent("测试", user_id="")


# ── 2. 工具层幂等 ───────────────────────────────────────────────────
def test_save_memory_is_idempotent_for_same_content():
    """同用户同内容重复保存：不新增行，且明确告知 LLM「已保存」。"""
    import json

    from dotenv import load_dotenv

    load_dotenv("d:/GuangH-App/agent/.env", override=False)
    from tools.memory_tools import save_memory

    uid = "test-idempotent-user"
    request_context.set(new_context(method="test", user_id=uid))
    text = "幂等回归测试：黄色台灯 88号"
    first = json.loads(str(save_memory.invoke({"content": text})))
    second = json.loads(str(save_memory.invoke({"content": text})))
    assert first.get("success") is True
    assert second.get("duplicate") is True
    assert second.get("memory_id") == first.get("memory_id")
    assert "无需重复保存" in second.get("message", "")


# ── 3. DB 取值 JSON 安全 ────────────────────────────────────────────
def test_db_value_normalizes_datetime():
    from datetime import datetime

    v = _db_value({"created_at": datetime(2026, 10, 4, 12, 30, 15), "n": 1})
    assert isinstance(v["created_at"], str)
    assert v["created_at"].startswith("2026-10-04")
    assert v["n"] == 1


def test_db_value_passes_through_plain_types():
    assert _db_value("x") == "x"
    assert _db_value(3) == 3
    assert _db_value(None) is None
    assert _db_value([1, 2]) == [1, 2]


# ── 4. PostgREST 通配语义 ────────────────────────────────────────────
def test_pg_pattern_converts_star_and_escapes():
    """`*` → SQL 通配；用户输入里的 % / _ 必须转义，否则会变成通配符。"""
    assert _pg_pattern("*灯*") == "%灯%"
    assert _pg_pattern("100%") == "100\\%"
    assert _pg_pattern("a_b") == "a\\_b"


def test_ilike_query_does_not_raise():
    """`ilike` 链路可用（迁移遗留缺陷：LocalTableQuery 原本没这个方法）。"""
    from dotenv import load_dotenv

    load_dotenv("d:/GuangH-App/agent/.env", override=False)
    from storage.database.local_client import create_local_client

    client = create_local_client()
    rows = client.table("memories").select("id").ilike("content", "*不存在xyz*").limit(1).execute()
    assert rows.data is not None