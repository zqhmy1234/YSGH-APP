"""`web/api_routes` 的回归测试（2026-10-06）。

## 覆盖的本轮修复

1. **`/api/memories?category=` 必 500**（在线故障）：原实现用 PostgREST 的
   **关系路径**过滤 `eq("memory_categories.name", x)`，而本仓数据层是
   `local_client` 薄层——不带括号的 `a.b` 会被当**列名**直拼，`getattr`
   取不到就抛 `ValueError: 表 memories 上不存在列 ...` ⇒ 端点必 500。
   修法：先查 `memory_categories.id`，再按 `category_id` 过滤（与
   `memory_tools._category_id` 同一修法）。
2. **分类名不存在时返回空集**而不是退化成"不过滤"——后者会把全部记忆
   当成该分类返回，是更隐蔽的假数据。
3. **3 处 `str(e)` 回显**：`main.py` 早已按"异常原文只进日志"收口，
   但这个 web 面漏了——同一仓库两套口径，正是"加固漏掉旁路"的反例。

## 为什么用最小 app 而不是 import `main.app`

`main.py` 的 lifespan 会做启动自检（校验 DATABASE_URL、打印配置、
`assert_production_token_config`），把整条服务启动拉进测试只会让失败原因
变得含混。这里只挂 `web_router`，聚焦被测对象。

## 鉴权口径

`require_auth` 读 `AGENT_SERVICE_TOKEN`（来自 agent/.env，经 conftest 加载）。
本机该值已配置 ⇒ 请求必须带 `X-Agent-Token`，否则 401。测试从环境取，
不硬编码、不打印。
"""
from __future__ import annotations

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web import api_routes as ar


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(ar.router)
    return TestClient(app)


@pytest.fixture(scope="module")
def auth_headers():
    token = (os.getenv("AGENT_SERVICE_TOKEN") or "").strip()
    return {"X-Agent-Token": token, "X-User-Id": "test_api_routes_user"}


# ── 1. 分类过滤（本轮修的在线 500） ──────────────────────────────────
def test_memories_with_unknown_category_returns_empty_not_500(client, auth_headers):
    """分类名不存在 ⇒ **空集**，不是 500，也不是"不过滤"。

    三种可能的结果里只有空集是对的：
    · 500 —— 修复前的行为（关系路径过滤在本薄层必抛 ValueError）
    · 返回全部记忆 —— 退化成不过滤，把别的分类的记忆当成该分类返回（假数据）
    · 返回空集 —— 分类不存在 ⇒ 该分类下必然无数据
    """
    r = client.get("/api/memories", params={"category": "绝对不存在的分类zz"},
                   headers=auth_headers)
    assert r.status_code == 200, f"修复前这里是 500：{r.text[:200]}"
    body = r.json()
    assert body["success"] is True
    assert body["data"] == []
    assert body["pagination"]["total"] == 0


def test_memories_with_real_category_filters(client, auth_headers):
    """存在的分类 ⇒ 只返回该分类的记忆（真库集成，验证过滤方向没写反）。"""
    from storage.database.supabase_client import get_supabase_client

    client_db = get_supabase_client()
    uid = "test_api_routes_user"
    cat = client_db.table("memory_categories").select("id").eq("name", "事件").limit(1).execute()
    cat_id = cat.data[0]["id"] if cat.data else None
    assert cat_id is not None, "memory_categories 里应有「事件」（SOP 初始枚举）"

    marker = "api_routes_probe_marker_20261006"
    inserted = client_db.table("memories").insert({
        "user_id": uid, "content": marker, "content_format": "text",
        "mood": "开心", "category_id": cat_id,
    }).execute()
    try:
        r = client.get("/api/memories", params={"category": "事件"}, headers=auth_headers)
        assert r.status_code == 200
        items = r.json()["data"]
        assert any(item["content"] == marker for item in items), (
            "插入该分类的记忆却没被过滤出来 ⇒ 过滤方向/键名有误"
        )
        # 反向：换个分类查不到这条
        r2 = client.get("/api/memories", params={"category": "地点"}, headers=auth_headers)
        assert r2.status_code == 200
        assert not any(item["content"] == marker for item in r2.json()["data"])
    finally:
        for row in (inserted.data or []):
            client_db.table("memories").delete().eq("id", row["id"]).execute()


# ── 2. 异常原文不得回显（3 处统一收口后的护栏） ────────────────────────
@pytest.mark.parametrize("path", ["/api/dashboard", "/api/memories", "/api/collections"])
def test_error_responses_do_not_echo_exception_text(client, auth_headers, monkeypatch, path):
    """数据层抛错时，响应体不得带 DSN/路径/表名——与 `/v1/chat` 同一口径。

    修复前这 3 个端点都写 `{"message": str(e)}`；而异常原文里可能含
    连接串（带密码）、服务器绝对路径、SQL 片段。这个响应体会进前端与
    我们后端的日志。
    """
    dsn = "postgresql+psycopg://user:hunter2@10.0.0.5:5432/yishu"

    class _Boom(Exception):
        pass

    def broken_client():
        class _Broken:
            def table(self, *_a, **_k):
                raise _Boom(f"connection to {dsn} failed: SELECT * FROM memories")

        return _Broken()

    monkeypatch.setattr(ar, "get_supabase_client", broken_client)
    r = client.get(path, headers=auth_headers)
    assert r.status_code == 500
    text = r.text
    assert "hunter2" not in text
    assert "://" not in text
    assert "SELECT" not in text
    assert "memories" not in text
    # 仍是可读的失败响应，而不是空体
    body = r.json()
    assert body["success"] is False
    assert body["message"]
    # 对外文案不含原文，但日志侧要留全量（caplog 在别的用例验证过口径）


def test_category_lookup_swallows_db_errors(client, auth_headers, monkeypatch):
    """`_category_id` 自身必须吞掉数据层错误并返回 None（而不是抛给端点变 500）。

    ⚠️ 本用例的写法有讲究：**不能** monkeypatch 掉 `_category_id` 本身——
    那会绕过它内部的 try/except，测的是假场景（我第一次就写错了：
    替换成会抛异常的函数，然后惊讶于端点 500）。正确做法是给它一个
    会抛错的 client，验证**它内部的兜底**生效。
    """
    dsn = "postgresql+psycopg://user:hunter2@10.0.0.5:5432/yishu"

    class _Broken:
        def table(self, *_a, **_k):
            raise RuntimeError(f"connection to {dsn} failed")

    # 直接调用被测函数（不走 HTTP），锁住"兜底返回 None"这一契约
    assert ar._category_id(_Broken(), "事件") is None

    # 端到端：分类查询失败时列表端点仍是 200 空集（该分类下无数据）
    monkeypatch.setattr(ar, "_category_id", lambda _c, _n: None)
    r = client.get("/api/memories", params={"category": "事件"}, headers=auth_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"] == []


# ── 3. 鉴权口径仍生效（防回归） ──────────────────────────────────────
def test_missing_token_is_rejected_when_token_configured(client, auth_headers):
    """token 已配置（本机 .env 如此）⇒ 不带 header 必须 401。

    这是 2026-10-04 收口的越权点（`/api/memories?user_id=<别人的>` 可读任何人
    记忆），别因为后来加分类过滤把它弄丢。
    """
    if not (os.getenv("AGENT_SERVICE_TOKEN") or "").strip():
        pytest.skip("AGENT_SERVICE_TOKEN 未配置（fail-open 场景），本用例不适用")
    r = client.get("/api/memories", headers={"X-User-Id": "someone"})
    assert r.status_code == 401


def test_missing_user_id_is_rejected(client):
    """无 user_id ⇒ 400（fail-closed，2026-10-04 移除 COZE_USER_ID 兜底后口径）。"""
    token = (os.getenv("AGENT_SERVICE_TOKEN") or "").strip()
    if not token:
        pytest.skip("AGENT_SERVICE_TOKEN 未配置，本用例不适用")
    r = client.get("/api/memories", headers={"X-Agent-Token": token})
    assert r.status_code == 400
