"""AG5 契约测试：`/api/v1/chat/messages` · `/chat/replies/{id}` · `/chat/conversations/{id}/messages`。

三条不可退让的断言（本项目已被栽过的坑）：
  1. **user_id 必须来自 JWT**（agent 服务不鉴权用户身份，本后端是唯一可信注入点）；
  2. **失败不伪造回复**（agent 不可用时只留用户消息，绝不生成"看似 AI 的回答"）；
  3. **会话隔离**（他人 conversation_id → 404，且**不得**触发 agent 调用）。

agent 服务在测试中被 monkeypatch（**不外呼网络**），故本文件可在无 agent 进程时跑。
"""
from __future__ import annotations

import uuid

from app.services.external.agent import AgentChatResult, AgentServiceError


def _mock_reply(monkeypatch, text: str = "收到啦，已经帮你记下了～", latency_ms: int = 4321):
    """把 agent 调用替换为确定性桩，并记录调用参数（用于断言可信注入与会话键）。"""
    calls: list[tuple[str, str, str]] = []

    def _fake(user_id: str, message: str, thread_id: str) -> AgentChatResult:
        calls.append((user_id, message, thread_id))
        return AgentChatResult(text=text, latency_ms=latency_ms, raw={"reply": text})

    monkeypatch.setattr("app.api.chat.call_agent_chat", _fake)
    return calls


def _mock_failure(monkeypatch, kind: str, status: int):
    def _fake(user_id: str, message: str, thread_id: str):
        raise AgentServiceError("stub failure", kind, status)

    monkeypatch.setattr("app.api.chat.call_agent_chat", _fake)


def test_requires_auth(client):
    """无 token → 401（对话域同样受保护，不因"智能体"而开口子）。"""
    resp = client.post("/api/v1/chat/messages", json={"content": "在吗"})
    assert resp.status_code == 401


def test_happy_path_persists_and_matches_contract(client, auth_headers, cleanup_user, monkeypatch):
    """正常路径：契约形状 + 两条消息落库 + user_id 可信注入 + thread_id=会话 ID。"""
    from app.db.session import SessionLocal
    from sqlalchemy import text

    user_id, headers = auth_headers("chat1")
    calls = _mock_reply(monkeypatch, text="✅ 已保存\n📝 内容：测试")
    try:
        resp = client.post(
            "/api/v1/chat/messages",
            json={"content": "帮我记住：今天读完一本书"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]

        # 契约形状（客户端 TabAi 依赖）
        assert set(data) >= {"conversation_id", "reply", "latency_ms"}
        assert set(data["reply"]) >= {"id", "kind", "text", "chips", "cards", "created_at"}
        assert data["reply"]["kind"] in {"bubble", "plain"}
        assert data["reply"]["text"].startswith("✅ 已保存")
        assert data["latency_ms"] >= 0

        # 可信注入：user_id 来自 JWT；thread_id 用会话 ID（保多轮上下文）
        assert len(calls) == 1
        assert calls[0][0] == user_id, "agent 侧 user_id 必须来自 JWT"
        assert calls[0][1] == "帮我记住：今天读完一本书"
        assert calls[0][2] == data["conversation_id"], "thread_id 必须是会话 ID（同一会话保上下文）"

        db = SessionLocal()
        try:
            rows = db.execute(
                text(
                    "SELECT role, kind, agent_latency_ms FROM chat_messages "
                    "WHERE user_id = :u ORDER BY created_at"
                ),
                {"u": user_id},
            ).all()
        finally:
            db.close()
        assert [r[0] for r in rows] == ["user", "assistant"]
        assert rows[1][2] == 4321  # agent 耗时落库（可观测）
    finally:
        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
            db.commit()
        finally:
            db.close()


def test_agent_unavailable_no_fake_reply(client, auth_headers, cleanup_user, monkeypatch):
    """**关键**：agent 不可用时返回 502 CHAT_001，且**只留用户消息**（绝不伪造回复）。"""
    from app.db.session import SessionLocal
    from sqlalchemy import text

    user_id, headers = auth_headers("chat2")
    _mock_failure(monkeypatch, "unavailable", 502)
    try:
        resp = client.post("/api/v1/chat/messages", json={"content": "在吗"}, headers=headers)
        assert resp.status_code == 502, resp.text
        body = resp.json()
        assert body.get("code") == "CHAT_001", body

        db = SessionLocal()
        try:
            rows = db.execute(
                text("SELECT role FROM chat_messages WHERE user_id = :u ORDER BY created_at"),
                {"u": user_id},
            ).all()
        finally:
            db.close()
        assert [r[0] for r in rows] == ["user"], "失败时不得写入任何 assistant 回复（不伪造）"
    finally:
        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
            db.commit()
        finally:
            db.close()


def test_agent_timeout_maps_504(client, auth_headers, cleanup_user, monkeypatch):
    """超时 → 504 CHAT_002（可重试语义与登记表一致）。"""
    user_id, headers = auth_headers("chat3")
    _mock_failure(monkeypatch, "unavailable", 504)
    try:
        resp = client.post("/api/v1/chat/messages", json={"content": "在吗"}, headers=headers)
        assert resp.status_code == 504, resp.text
        assert resp.json().get("code") == "CHAT_002", resp.json()
    finally:
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
            db.commit()
        finally:
            db.close()


def test_conversation_idor_404_and_agent_not_called(client, auth_headers, monkeypatch):
    """越权/不存在的会话 → 404 CHAT_003，且**不触发 agent 调用**（不把他人会话当自己的）。"""
    calls = _mock_reply(monkeypatch)
    _, headers = auth_headers("chat4")
    resp = client.post(
        "/api/v1/chat/messages",
        json={"conversation_id": str(uuid.uuid4()), "content": "在吗"},
        headers=headers,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "CHAT_003"
    assert calls == [], "会话非法时不得调用 agent"


def test_reply_fetch_and_history(client, auth_headers, cleanup_user, monkeypatch):
    """回复可按 ID 取回；会话历史按时间正序返回。"""
    user_id, headers = auth_headers("chat5")
    _mock_reply(monkeypatch, text="第一答")
    try:
        first = client.post("/api/v1/chat/messages", json={"content": "第一问"}, headers=headers).json()["data"]
        reply_id = first["reply"]["id"]
        conv_id = first["conversation_id"]

        got = client.get(f"/api/v1/chat/replies/{reply_id}", headers=headers)
        assert got.status_code == 200, got.text
        assert got.json()["data"]["text"] == "第一答"

        # 第二轮：带上 conversation_id → 不新建会话
        second = client.post(
            "/api/v1/chat/messages",
            json={"conversation_id": conv_id, "content": "第二问"},
            headers=headers,
        ).json()["data"]
        assert second["conversation_id"] == conv_id

        hist = client.get(f"/api/v1/chat/conversations/{conv_id}/messages", headers=headers)
        assert hist.status_code == 200, hist.text
        msgs = hist.json()["data"]["messages"]
        assert [m["text"] for m in msgs] == ["第一问", "第一答", "第二问", "第一答"]

        # 未知 / 畸形 ID → 404（不区分"不存在"与"无权"）
        assert client.get(f"/api/v1/chat/replies/{uuid.uuid4()}", headers=headers).status_code == 404
        assert client.get("/api/v1/chat/replies/not-a-uuid", headers=headers).status_code == 404
    finally:
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
            db.commit()
        finally:
            db.close()


def test_empty_content_rejected(client, auth_headers):
    """空内容 → 422（契约层校验，不进业务逻辑）。"""
    _, headers = auth_headers("chat6")
    resp = client.post("/api/v1/chat/messages", json={"content": ""}, headers=headers)
    assert resp.status_code == 422
