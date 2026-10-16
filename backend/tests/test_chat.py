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

    def _fake(user_id: str, message: str, conversation_id: str) -> AgentChatResult:
        calls.append((user_id, message, conversation_id))
        return AgentChatResult(text=text, latency_ms=latency_ms, raw={"reply": text})

    monkeypatch.setattr("app.api.chat.call_agent_chat", _fake)
    return calls


def _mock_failure(monkeypatch, kind: str, status: int):
    def _fake(user_id: str, message: str, conversation_id: str):
        raise AgentServiceError("stub failure", kind, status)

    monkeypatch.setattr("app.api.chat.call_agent_chat", _fake)


def test_requires_auth(client):
    """无 token → 401（对话域同样受保护，不因"智能体"而开口子）。"""
    resp = client.post("/api/v1/chat/messages", json={"content": "在吗"})
    assert resp.status_code == 401


def test_happy_path_persists_and_matches_contract(client, auth_headers, cleanup_user, monkeypatch):
    """正常路径：契约形状 + 两条消息落库 + user_id 可信注入 + conversation_id 透传。"""
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

        # 可信注入：user_id 来自 JWT；conversation_id 透传（agent 侧按
        # f"{user_id}:{conversation_id}" 派生 thread_id 满足归属校验——
        # 见 external/agent.py，2026-10-08 服务间实联调修复）
        assert len(calls) == 1
        assert calls[0][0] == user_id, "agent 侧 user_id 必须来自 JWT"
        assert calls[0][1] == "帮我记住：今天读完一本书"
        assert calls[0][2] == data["conversation_id"], "conversation_id 必须透传（thread 由 agent 调用方派生）"

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


# ═══════════ 附件契约最小可用（2026-10-16 · 台账 §7.14 / 审计 A1-A3·D7）═══════════
#
# 钉死三件事（都是"声明了但两端没实现"的旧缺口，见台账 §7.14）：
#   ① 附件（图片）→ agent 收到的**文本提示**取自既有 caption 字段（不上视觉模型）；
#   ② 附件提示**不得**泄漏他人内容（content_id 越权 ⇒ 只承认"内容暂不可读"）；
#   ③ `include_profile` 是隐私开关的**真消费者**：False ⇒ 后端不携带画像上下文。


def _seed_content(user_id: str, caption: str) -> str:
    """建一条本人内容（模拟"图片已走既有上传链落库"），返回 content_id。"""
    from app.db.models import Content
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        row = Content(user_id=user_id, content_type="photo", text=caption, status="done")
        db.add(row)
        db.commit()
        db.refresh(row)
        return str(row.id)
    finally:
        db.close()


def _seed_profile(user_id: str, dimensions: dict) -> None:
    """建画像（`include_profile` 的消费者读的就是 user_profile.dimensions）。"""
    from app.db.models import UserProfile
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        db.merge(UserProfile(user_id=user_id, dimensions=dimensions))
        db.commit()
    finally:
        db.close()


def _capture_agent(monkeypatch) -> dict:
    """捕获 agent 调用（message + 附件 kwargs）；桩返回固定回复。"""
    captured: dict = {}

    def _fake(user_id: str, message: str, conversation_id: str, attachments=None):  # noqa: ARG001
        captured["message"] = message
        captured["attachments"] = attachments
        return AgentChatResult(text="看到了", latency_ms=5, raw={"reply": "看到了"})

    monkeypatch.setattr("app.api.chat.call_agent_chat", _fake)
    return captured


def test_attachment_caption_reaches_agent_as_text(client, auth_headers, cleanup_user, monkeypatch):
    """图片附件：caption（既有字段）→ agent 的文本提示；结构化附件同时透传（三层一致）。"""
    from app.db.session import SessionLocal

    user_id, headers = auth_headers("chat8")
    cid = _seed_content(user_id, "湖边黄昏，水面反着金光")
    captured = _capture_agent(monkeypatch)
    try:
        resp = client.post(
            "/api/v1/chat/messages",
            json={"content": "这张照片怎么样", "attachments": [{"kind": "image", "content_id": cid}]},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        msg = captured["message"]
        assert msg.startswith("这张照片怎么样"), "用户正文必须在最前（上下文不得挤掉正文）"
        assert "用户附了一张图，内容是：湖边黄昏，水面反着金光" in msg
        # 三层一致：结构化附件原样透传给 agent（agent 侧仅留痕，不解析）
        assert captured["attachments"] == [
            {"kind": "image", "content_id": cid, "name": None, "size": None}
        ]
    finally:
        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
            db.commit()
        finally:
            db.close()


def test_attachment_other_users_content_not_leaked(client, auth_headers, cleanup_user, monkeypatch):
    """他人的 content_id ⇒ 不得把其 caption 喂给 AI（越权即视为不可读，且不报错）。"""
    from app.db.session import SessionLocal

    victim_id, _ = auth_headers("chat9victim")
    user_id, headers = auth_headers("chat9")
    cid = _seed_content(victim_id, "别人的私密照片描述")
    captured = _capture_agent(monkeypatch)
    try:
        resp = client.post(
            "/api/v1/chat/messages",
            json={"content": "看看", "attachments": [{"kind": "image", "content_id": cid}]},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert "别人的私密照片描述" not in captured["message"], "越权附件不得泄漏内容"
        assert "内容暂不可读" in captured["message"], "读不到须如实说，不得编造"
    finally:
        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
            cleanup_user(db, victim_id)
            db.commit()
        finally:
            db.close()


def test_include_profile_toggles_profile_context(client, auth_headers, cleanup_user, monkeypatch):
    """`include_profile` 真的生效：True 带画像、False 只发正文（隐私开关的消费者）。"""
    from app.db.session import SessionLocal

    user_id, headers = auth_headers("chat10")
    _seed_profile(user_id, {"relation_core": "家人", "values_priority": "健康"})
    captured = _capture_agent(monkeypatch)
    try:
        on = client.post("/api/v1/chat/messages", json={"content": "在吗"}, headers=headers)
        assert on.status_code == 200, on.text
        assert "【用户画像】" in captured["message"]
        assert "relation_core=家人" in captured["message"]

        off = client.post(
            "/api/v1/chat/messages",
            json={"content": "在吗", "include_profile": False},
            headers=headers,
        )
        assert off.status_code == 200, off.text
        assert captured["message"] == "在吗", "include_profile=False ⇒ 后端不携带画像上下文"
    finally:
        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
            db.commit()
        finally:
            db.close()


def test_stream_attachment_hint_and_include_profile(client, auth_headers, cleanup_user, monkeypatch):
    """流式端点（客户端**唯一**路径）与附件契约/隐私开关同口径。"""
    from app.db.session import SessionLocal

    user_id, headers = auth_headers("chat11")
    cid = _seed_content(user_id, "生日蛋糕上的蜡烛")
    _seed_profile(user_id, {"relation_core": "家人"})
    captured: dict = {}

    def _fake_stream(user_id: str, message: str, conversation_id: str, attachments=None):  # noqa: ARG001
        captured["message"] = message
        captured["attachments"] = attachments
        yield ("done", {"reply": "看到蛋糕了", "latency_ms": 3})

    monkeypatch.setattr("app.api.chat.stream_agent_chat", _fake_stream)
    try:
        resp = client.post(
            "/api/v1/chat/messages/stream",
            json={
                "content": "看这张",
                "attachments": [{"kind": "image", "content_id": cid}],
                "include_profile": False,
            },
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert "用户附了一张图，内容是：生日蛋糕上的蜡烛" in captured["message"]
        assert "【用户画像】" not in captured["message"], "include_profile=False 在流式端点同样生效"
        assert captured["attachments"] == [
            {"kind": "image", "content_id": cid, "name": None, "size": None}
        ]
    finally:
        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
            db.commit()
        finally:
            db.close()


def test_cards_kind_from_agent_meta(client, auth_headers, cleanup_user, monkeypatch):
    """非流式端点也要把 agent 的结构化附加映射成 `kind=cards`（与流式端点同形态）。

    为什么要单独钉：两个端点各自有一份 kind/payload 组装逻辑，只测其中一个，
    另一个就会在"改了 agent 的 meta 字段名"时静默降级成纯文本（形态不一致，
    同一条回复走不同端点长得不一样）。
    """
    from app.services.external.agent import AgentChatResult

    user_id, headers = auth_headers("chat7")

    def _fake(user_id: str, message: str, conversation_id: str) -> AgentChatResult:  # noqa: ARG001
        return AgentChatResult(
            text="已经帮你记下了",
            latency_ms=11,
            raw={"reply": "已经帮你记下了"},
            meta={
                "cards": [{"title": "湖边散步", "meta": "情绪 · 开心", "img": "", "topics": ["生活"]}],
                "chips": ["看看「生活」相关的记忆"],
            },
        )

    monkeypatch.setattr("app.api.chat.call_agent_chat", _fake)
    try:
        resp = client.post("/api/v1/chat/messages", json={"content": "记一下"}, headers=headers)
        assert resp.status_code == 200, resp.text
        reply = resp.json()["data"]["reply"]
        assert reply["kind"] == "cards"
        assert reply["cards"][0]["title"] == "湖边散步"
        assert reply["chips"] == ["看看「生活」相关的记忆"]
    finally:
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
            db.commit()
        finally:
            db.close()
