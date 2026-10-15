"""用户数据隔离测试（2026-10-06 重写为确定性版本）。

## 为什么重写（旧版的四个毛病）

1. **没有任何断言** —— 旧版"隔离验证"一段只是 print 布尔值，打印完就结束。
   也就是说它**永远绿**（只要不抛异常），从未验证过任何东西，
   是一段穿着 pytest 外衣的手工演示脚本。
2. **依赖真实 LLM** —— 为验证"数据按 user_id 隔离"这种纯数据层性质，
   却让 5 次真实 LLM 调用当载体：慢（80s+）、网络一抖就假红
   （本会话实测红过一次，重跑 3 次又全绿）、还烧 token。
3. **污染数据库** —— 往 memories 写 user_A / user_B 两条真实数据，从不清理。
4. **断言对象选错** —— 它检查 LLM 回复文本里是否出现"香山"，而隔离的真正
   落点是 SQL 的 .eq("user_id", ...)。回复文本受模型措辞影响，以文本包含性
   做隔离判定既不严谨也不稳定。

## 新版测什么

隔离的执行点在工具层（_get_user_id() 从 request_context 取 user_id，
再进每条 SQL 的 .eq("user_id", ...)）。故直接在工具层、用真实数据库、
零 LLM 地验证同一性质，并且真断言 + 真清理：

- A 存的记忆，A 搜得到
- A 存的记忆，B 搜不到（核心隔离性质）
- get_recent_memories 这条独立读路径同样隔离
- run_agent 对缺失 user_id fail-closed（历史上跨用户串档的根源）

工具层直调与 HTTP 链路用的是同一个 request_context 通道（/v1/chat 也是
new_context(...) + request_context.set(ctx)），所以这里验证的就是生产路径。
"""
from __future__ import annotations

import json
import uuid

import pytest

from tools.memory_tools import get_recent_memories, save_memory, search_memories
from yishu.context import new_context, request_context


@pytest.fixture
def user_a() -> str:
    return f"iso_A_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def user_b() -> str:
    return f"iso_B_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def as_user():
    """把 user_id 装进 request_context（与 /v1/chat 生产路径同一通道）。"""

    def _as(user_id: str):
        ctx = new_context(method="test", user_id=user_id)
        ctx.user_id = user_id
        request_context.set(ctx)
        return user_id

    yield _as
    # 收尾：清掉上下文，避免串到下一个用例
    request_context.set(None)


def _invoke(tool, **kwargs):
    """工具以 JSON 字符串返回，统一解包。"""
    return json.loads(tool.invoke(kwargs))


def _cleanup(user_ids: list[str]) -> None:
    from storage.database.supabase_client import get_supabase_client

    db = get_supabase_client()
    for uid in user_ids:
        db.table("memories").delete().eq("user_id", uid).execute()


def test_isolation_between_two_users(as_user, user_a, user_b):
    """核心性质：A 存的，B 搜不到；B 存的，A 也搜不到。"""
    marker_a = f"香山红叶隔离探针A_{uuid.uuid4().hex[:6]}"
    marker_b = f"技术分享隔离探针B_{uuid.uuid4().hex[:6]}"
    try:
        as_user(user_a)
        out_a = _invoke(save_memory, content=marker_a, category="事件")
        assert out_a["success"] is True, out_a

        as_user(user_b)
        out_b = _invoke(save_memory, content=marker_b, category="工作")
        assert out_b["success"] is True, out_b

        # A 搜自己的 → 找得到
        as_user(user_a)
        found_a = _invoke(search_memories, query=marker_a[:20])
        assert any(marker_a in (m["content"] or "") for m in found_a["memories"]), (
            "A 自己存的记忆，A 却搜不到 ⇒ 检索按 user_id 过滤的方向写反了"
        )

        # B 搜 A 的 marker → 必须为空（本文件要锁的核心性质）
        as_user(user_b)
        leak_b = _invoke(search_memories, query=marker_a[:20])
        assert not any(marker_a in (m["content"] or "") for m in leak_b["memories"]), (
            f"跨用户泄漏：B 搜到了 A 的记忆（{len(leak_b['memories'])} 条候选）"
        )

        # A 搜 B 的 marker → 同样必须为空
        as_user(user_a)
        leak_a = _invoke(search_memories, query=marker_b[:20])
        assert not any(marker_b in (m["content"] or "") for m in leak_a["memories"]), (
            "反向也泄漏：A 搜到了 B 的记忆"
        )
    finally:
        _cleanup([user_a, user_b])


def test_recent_memories_is_isolated_too(as_user, user_a, user_b):
    """get_recent_memories 是另一条读路径，同样必须按 user_id 隔离。

    单修 search_memories 不够——每个读工具都是独立的 .eq("user_id") 调用点
    （Agent 2 逐条核对过 13 个工具，守卫要把这个结论钉住）。
    """
    marker_a = f"近期记忆隔离探针A_{uuid.uuid4().hex[:6]}"
    try:
        as_user(user_a)
        assert _invoke(save_memory, content=marker_a, category="事件")["success"]

        as_user(user_b)
        recent_b = _invoke(get_recent_memories, days=7, limit=50)
        assert not any(marker_a in (m["content"] or "") for m in recent_b["memories"]), (
            "get_recent_memories 跨用户泄漏"
        )

        as_user(user_a)
        recent_a = _invoke(get_recent_memories, days=7, limit=50)
        assert any(marker_a in (m["content"] or "") for m in recent_a["memories"]), (
            "A 自己的近期记忆，A 自己也拿不到"
        )
    finally:
        _cleanup([user_a, user_b])


def test_missing_user_id_is_rejected_not_silently_shared():
    """run_agent 对缺失 user_id 必须 fail-closed。

    历史上签名默认值是 "default"，等于「没注入用户就落到共享桶」——
    save_memory 写进公共身份、search 能搜到别人的记忆，而且**不报错**。
    改成拒绝后必须有测试钉住，否则哪天有人把默认值加回来无人知晓。
    （这个用例不需要 LLM：它在 invoke 之前就该抛错。）
    """
    from agents.agent import run_agent

    with pytest.raises(ValueError, match="user_id"):
        run_agent("随便说点什么", user_id="   ")