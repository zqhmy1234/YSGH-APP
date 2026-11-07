"""`POST /api/v1/chat/messages/stream`（SSE）契约测试（AG6 余项 · 2026-10-09）。

与 `test_chat.py`（非流式）**成对**，钉死的是流式形态下**必须不打折**的三件事：
  1. 用户消息先落库 + 失败不伪造回复（agent 不可用 ⇒ 只有 user 行）；
  2. **预览不得泄漏被拦内容**（规则层门控：命中即中断且不再推该文本）；
  3. 终稿仍走完整护栏（CHAT_004 ⇒ 不落库、不返回）；
  4. 帧契约（status/preview/replace/tool/done/error 的事件名与字段）与「预览是累计全文」。

agent 服务在测试中被 monkeypatch（**不外呼网络**），故本文件可在无 agent 进程时跑。
"""
from __future__ import annotations

import json
import uuid

from app.services.external.agent import AgentServiceError


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """把 SSE 响应体解析成 `[(event, data)]`（注释帧忽略）。"""
    out: list[tuple[str, dict]] = []
    for frame in text.split("\n\n"):
        lines = [ln for ln in frame.splitlines() if ln.strip()]
        if not lines or lines[0].startswith(":"):
            continue
        event, data = "", ""
        for ln in lines:
            if ln.startswith("event: "):
                event = ln[len("event: ") :]
            elif ln.startswith("data: "):
                data = ln[len("data: ") :]
        out.append((event, json.loads(data) if data else {}))
    return out


def _mock_stream(monkeypatch, events, error: Exception | None = None):
    """把 `stream_agent_chat` 换成确定性桩（可注入异常），并记录调用参数。"""
    calls: list[tuple[str, str, str]] = []

    def _fake(user_id: str, message: str, conversation_id: str):
        calls.append((user_id, message, conversation_id))
        yield from events
        if error is not None:
            raise error

    monkeypatch.setattr("app.api.chat.stream_agent_chat", _fake)
    return calls


def _rows(user_id: str) -> list[tuple[str, str]]:
    from app.db.session import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        return db.execute(
            text("SELECT role, kind FROM chat_messages WHERE user_id = :u ORDER BY created_at"),
            {"u": user_id},
        ).all()
    finally:
        db.close()


def test_requires_auth(client):
    """无 token → 401（流式端点同样受保护）。"""
    resp = client.post("/api/v1/chat/messages/stream", json={"content": "在吗"})
    assert resp.status_code == 401


def test_happy_path_frames_and_persist(client, auth_headers, cleanup_user, monkeypatch):
    """正常路径：帧序齐全 + 预览累计 + 终稿落库（kind 由终稿文本判定）。"""
    user_id, headers = auth_headers("chatstream1")
    _mock_stream(
        monkeypatch,
        [
            ("status", {"stage": "accepted"}),
            ("delta", "你好"),
            ("tool", {"phase": "started", "name": "search_memories"}),
            ("delta", "呀～"),
            ("done", {"reply": "你好呀～", "latency_ms": 1234}),
        ],
    )
    try:
        resp = client.post(
            "/api/v1/chat/messages/stream",
            json={"content": "你好"},
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("text/event-stream")
        events = _parse_sse(resp.text)
        assert [e for e, _ in events] == ["status", "preview", "tool", "preview", "done"]

        # 预览是**累计全文**（不是增量）：第二帧必须是完整"你好呀～"
        previews = [p["text"] for e, p in events if e == "preview"]
        assert previews == ["你好", "你好呀～"]

        done = [p for e, p in events if e == "done"][0]
        assert done["reply"]["text"] == "你好呀～"
        assert done["reply"]["kind"] == "bubble"
        assert set(done["reply"]) >= {"id", "kind", "text", "chips", "cards", "created_at"}
        assert done["conversation_id"]
        assert done["latency_ms"] >= 0

        assert [(r, k) for r, k in _rows(user_id)] == [("user", "user"), ("assistant", "bubble")]
    finally:
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
            db.commit()
        finally:
            db.close()


def test_agent_unavailable_no_fake_reply(client, auth_headers, cleanup_user, monkeypatch):
    """**关键**：agent 不可用 ⇒ error 帧 CHAT_001/002，且**只留用户消息**。"""
    user_id, headers = auth_headers("chatstream2")
    _mock_stream(
        monkeypatch,
        [("status", {"stage": "accepted"}), ("delta", "半截")],
        error=AgentServiceError("stub failure", "unavailable", 504),
    )
    try:
        resp = client.post("/api/v1/chat/messages/stream", json={"content": "在吗"}, headers=headers)
        events = _parse_sse(resp.text)
        assert [e for e, _ in events][-1] == "error"
        err = events[-1][1]
        assert err["code"] == "CHAT_002"
        assert "done" not in [e for e, _ in events]
        assert [r for r, _ in _rows(user_id)] == ["user"], "失败时不得写入任何 assistant 回复"
    finally:
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
            db.commit()
        finally:
            db.close()


def test_preview_rule_layer_blocks_leak(client, auth_headers, cleanup_user, monkeypatch):
    """**关键**：规则层命中 ⇒ 该文本**一帧都不推**，且不落库（护栏拦截不得泄漏预览）。"""
    user_id, headers = auth_headers("chatstream3")
    _mock_stream(
        monkeypatch,
        [
            ("status", {"stage": "accepted"}),
            ("delta", "正常开头"),
            ("delta", "违规尾巴"),
            ("delta", "更多违规"),
            ("done", {"reply": "正常开头违规尾巴更多违规", "latency_ms": 10}),
        ],
    )

    from app.api import chat as chat_api

    real_rule_screen = chat_api.rule_screen

    def _fake_rule_screen(text: str) -> dict:
        if "违规" in text:
            return {"pass": False, "action": "reject", "matched": ["stub"], "categories": [],
                    "detector": "rule", "reason": "rule-reject"}
        return real_rule_screen(text)

    monkeypatch.setattr("app.api.chat.rule_screen", _fake_rule_screen)

    try:
        resp = client.post("/api/v1/chat/messages/stream", json={"content": "说点啥"}, headers=headers)
        events = _parse_sse(resp.text)
        previews = [p["text"] for e, p in events if e == "preview"]
        assert previews == ["正常开头"], "命中规则层的文本不得出现在任何 preview 帧里"
        assert all("违规" not in t for t in previews)
        err = [p for e, p in events if e == "error"][0]
        assert err["code"] == "CHAT_004"
        assert [r for r, _ in _rows(user_id)] == ["user"]
    finally:
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
            db.commit()
        finally:
            db.close()


def test_final_guard_blocks_persist(client, auth_headers, cleanup_user, monkeypatch):
    """终稿护栏命中 ⇒ CHAT_004、不落库（与非流式同口径）。"""
    user_id, headers = auth_headers("chatstream4")
    _mock_stream(
        monkeypatch,
        [("status", {"stage": "accepted"}), ("done", {"reply": "某段文本", "latency_ms": 10})],
    )
    monkeypatch.setattr(
        "app.api.chat.screen_generated",
        lambda text, deep=False: {"pass": False, "action": "reject", "matched": ["stub"],
                                  "categories": [], "detector": "stub", "reason": "stub"},
    )
    try:
        resp = client.post("/api/v1/chat/messages/stream", json={"content": "在吗"}, headers=headers)
        events = _parse_sse(resp.text)
        assert [p for e, p in events if e == "error"][0]["code"] == "CHAT_004"
        assert "done" not in [e for e, _ in events]
        assert [r for r, _ in _rows(user_id)] == ["user"]
    finally:
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
            db.commit()
        finally:
            db.close()


def test_replace_frame_overwrites_preview(client, auth_headers, cleanup_user, monkeypatch):
    """校验重试 ⇒ `replace` 帧（整段），客户端据此覆盖预览；终稿以 replace 为准。"""
    user_id, headers = auth_headers("chatstream5")
    _mock_stream(
        monkeypatch,
        [
            ("status", {"stage": "accepted"}),
            ("delta", "hello 我记下了"),
            ("replace", "好的，已经帮你记下了"),
            ("done", {"reply": "好的，已经帮你记下了", "latency_ms": 20}),
        ],
    )
    try:
        resp = client.post("/api/v1/chat/messages/stream", json={"content": "记一下"}, headers=headers)
        events = _parse_sse(resp.text)
        assert [e for e, _ in events] == ["status", "preview", "replace", "done"]
        assert [p["text"] for e, p in events if e == "replace"] == ["好的，已经帮你记下了"]
        done = [p for e, p in events if e == "done"][0]
        assert done["reply"]["text"] == "好的，已经帮你记下了"
        assert done["reply"]["kind"] == "bubble"
    finally:
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
            db.commit()
        finally:
            db.close()


def test_cards_kind_persisted_and_returned_in_history(client, auth_headers, cleanup_user, monkeypatch):
    """结构化附加（cards/chips）⇒ kind=cards、落 payload、终稿帧与历史回填都带 cards/chips。

    三条一起断言是有意的：**同一条回复在不同时间点必须长得一样**——
    只测终稿帧会漏掉"重启 App 后卡片变纯文本"（历史回填不带 cards）这类降级。
    """
    user_id, headers = auth_headers("chatstream7")
    meta = {
        "cards": [
            {"title": "今天在湖边散步很开心", "meta": "情绪 · 开心 · 生活", "img": "",
             "category": "情绪", "mood": "开心", "topics": ["生活"]}
        ],
        "chips": ["看看「生活」相关的记忆", "再帮我记一条"],
    }
    _mock_stream(
        monkeypatch,
        [
            ("status", {"stage": "accepted"}),
            ("delta", "已经帮你记下了"),
            ("done", {"reply": "已经帮你记下了", "latency_ms": 9, "meta": meta}),
        ],
    )
    try:
        resp = client.post("/api/v1/chat/messages/stream", json={"content": "记一下"}, headers=headers)
        events = _parse_sse(resp.text)
        done = [p for e, p in events if e == "done"][0]
        assert done["reply"]["kind"] == "cards"
        assert done["reply"]["cards"][0]["title"] == "今天在湖边散步很开心"
        assert done["reply"]["chips"] == ["看看「生活」相关的记忆", "再帮我记一条"]
        conv_id = done["conversation_id"]

        hist = client.get(f"/api/v1/chat/conversations/{conv_id}/messages", headers=headers)
        assert hist.status_code == 200, hist.text
        assistant = [m for m in hist.json()["data"]["messages"] if m["role"] == "assistant"][0]
        assert assistant["kind"] == "cards"
        assert assistant["cards"][0]["meta"] == "情绪 · 开心 · 生活"
        assert assistant["chips"] == ["看看「生活」相关的记忆", "再帮我记一条"]

        assert [(r, k) for r, k in _rows(user_id)] == [("user", "user"), ("assistant", "cards")]
    finally:
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
            db.commit()
        finally:
            db.close()


def test_chips_only_reply_uses_plain_kind(client, auth_headers, cleanup_user, monkeypatch):
    """只有 chips（无 cards）⇒ kind=plain：客户端**只有 plain/cards 分支**渲染 chips，
    单行文本若判成 bubble，chips 会静默消失。"""
    user_id, headers = auth_headers("chatstream8")
    _mock_stream(
        monkeypatch,
        [
            ("status", {"stage": "accepted"}),
            ("done", {"reply": "好的呀", "latency_ms": 5, "meta": {"cards": [], "chips": ["再问一句"]}}),
        ],
    )
    try:
        resp = client.post("/api/v1/chat/messages/stream", json={"content": "在吗"}, headers=headers)
        done = [p for e, p in _parse_sse(resp.text) if e == "done"][0]
        assert done["reply"]["kind"] == "plain"
        assert done["reply"]["chips"] == ["再问一句"]
    finally:
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
            db.commit()
        finally:
            db.close()


def test_stream_ends_without_done_never_persists_empty_reply(
    client, auth_headers, cleanup_user, monkeypatch
):
    """**关键**：agent 流提前结束（没 `done` 帧）⇒ error 帧 CHAT_001/002，**不得落一条空回复**。

    这是流式特有的新失败面：非流式下"没回复"会直接抛异常，而流式下"监听到了 preview 但
    终稿没来"很容易被当成正常收尾 ⇒ 落一条 content='' 的 assistant 行（用户看到空气泡、
    历史永久留脏数据）。适配器层已改为把"未见 done"当 unavailable 抛出（见
    services/external/agent.py::stream_agent_chat 的 saw_done 守卫），本测试钉住这一行为。
    """
    user_id, headers = auth_headers("chatstream9")
    _mock_stream(
        monkeypatch,
        [("status", {"stage": "accepted"}), ("delta", "说到一半")],  # 故意不给 done
    )
    try:
        resp = client.post("/api/v1/chat/messages/stream", json={"content": "在吗"}, headers=headers)
        events = _parse_sse(resp.text)
        # 桩本身是"沉默结束"（没有抛错），端点会拿空终稿 ⇒ 必须自己兜住：不得落库
        kinds = [e for e, _ in events]
        assert "done" not in kinds
        if "error" in kinds:
            assert [p for e, p in events if e == "error"][0]["code"] in {"CHAT_001", "CHAT_002"}
        assert [r for r, _ in _rows(user_id)] == ["user"], "绝不能落空回复"
    finally:
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
            db.commit()
        finally:
            db.close()


def _fake_http_stream(monkeypatch, frames: list[str]):
    """把 httpx.Client 换成返回固定 SSE 行的假客户端（**不外呼网络**）。"""
    from app.services.external import agent as agent_mod

    class _FakeResp:
        status_code = 200

        def iter_lines(self):
            yield from frames

        def read(self) -> bytes:  # pragma: no cover —— 仅非 2xx 分支用
            return b""

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    class _FakeClient:
        def __init__(self, *_a, **_k) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def stream(self, *_a, **_k):
            return _FakeResp()

    monkeypatch.setattr(agent_mod.httpx, "Client", lambda *_a, **_k: _FakeClient())
    return agent_mod


def test_adapter_requires_done_frame(monkeypatch):
    """适配器：agent 流**没有 done 帧** ⇒ 抛 unavailable（别让上层拿空终稿去落库）。"""
    import pytest

    agent_mod = _fake_http_stream(
        monkeypatch, ["event: status", 'data: {"stage": "accepted"}', "", "event: delta", 'data: {"text": "半句"}', ""]
    )
    with pytest.raises(AgentServiceError) as exc:
        list(agent_mod.stream_agent_chat("u-stream", "在吗", "c-stream"))
    assert exc.value.http_status == 502


def test_adapter_parses_frames_and_ignores_ping(monkeypatch):
    """适配器：注释保活帧与 status 帧不该打断解析，done 帧带 meta 原样透传。"""
    agent_mod = _fake_http_stream(
        monkeypatch,
        [
            ": ping",
            "",
            "event: status",
            'data: {"stage": "accepted"}',
            "",
            "event: delta",
            'data: {"text": "你好"}',
            "",
            "event: done",
            'data: {"reply": "你好", "latency_ms": 12, "meta": {"cards": [], "chips": ["再来一句"]}}',
            "",
        ],
    )
    events = list(agent_mod.stream_agent_chat("u-stream", "在吗", "c-stream"))
    assert [k for k, _ in events] == ["status", "delta", "done"]
    assert events[-1][1]["meta"]["chips"] == ["再来一句"]


def test_conversation_idor_404_and_agent_not_called(client, auth_headers, monkeypatch):
    """越权/不存在的会话 ⇒ 404 CHAT_003，且**不触发 agent 调用**（HTTP 状态码仍真实）。"""
    calls = _mock_stream(monkeypatch, [("status", {"stage": "accepted"})])
    _, headers = auth_headers("chatstream6")
    resp = client.post(
        "/api/v1/chat/messages/stream",
        json={"conversation_id": str(uuid.uuid4()), "content": "在吗"},
        headers=headers,
    )
    assert resp.status_code == 404, resp.text
    assert resp.json().get("code") == "CHAT_003"
    assert calls == []
