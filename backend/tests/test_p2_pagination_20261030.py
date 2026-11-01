"""P2 分页与失败信号（2026-10-30 · B-7 / C-5 / D-11 / F-6）

覆盖范围（每条对应一个已登记的交互链路缺陷，见 docs/客户端交互链路勘错_20261030.md）：

  · B-7  `GET /events/timeline` 分页：**不带参数仍是数组**（向后兼容）+ 游标翻页
         不重不漏 + 非法游标 422。
  · C-5  `POST /search` 的 offset/limit/total/has_more 语义（用假召回，不依赖
         Qdrant/BGE-M3：本用例要验的是**切片窗口**，不是召回质量）。
  · D-11 `GET /chat/conversations/{id}/messages` 分页：先给最近 N 条、cursor 向前翻、
         出参恒时间正序、非法游标 422 CHAT_005。
  · F-6  画像重建：内容数 > 批大小（原 `limit=500` 兼作总量上限）时必须**全部处理**，
         `total` 是真实总数而不是批大小。

反例意识（这些用例都在防"看起来成功"的假修）：
  · 分页改造最典型的假修是"响应改成分页对象、老客户端拿到空数组" —— 故第一条就断言
    无参时 `data` 仍是数组。
  · 第二条假修是"offset 生效但 total 恒等于本页条数" —— 故断言 total 随窗口推进。
  · 第三条假修是"limit 只是 SQL 上限、超出的静默不处理" —— 故断言 processed == 真实总数。
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest


def _const_false() -> bool:
    """rerank_auto_enabled 的桩：恒 False（不用 lambda，规避 PLW0108）。"""
    return False


# ---------------------------------------------------------------------------
# B-7 · 时间轴分页
# ---------------------------------------------------------------------------


def _make_events(db, user, specs: list[datetime | None]) -> list:
    """按给定 start_time 造事件（None = 时间缺失，专测 NULL 段翻页）"""
    from app.db.models import Event

    events = []
    for idx, ts in enumerate(specs):
        ev = Event(
            user_id=user.id,
            level=1,
            title=f"P2-B7-{idx}",
            start_time=ts,
            status="confirmed",
        )
        db.add(ev)
        events.append(ev)
    db.commit()
    for ev in events:
        db.refresh(ev)
    return events


def _auth(user) -> dict:
    from app.core.security import create_access_token

    return {"Authorization": f"Bearer {create_access_token(str(user.id))}"}


def test_timeline_without_params_still_returns_plain_array(db_user):
    """B-7 向后兼容：不传 limit/cursor ⇒ `data` 仍是数组（老客户端/存量脚本的解析口径）"""
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    base = datetime.now(UTC)
    created = _make_events(db, user, [base, base - timedelta(days=1)])

    client = TestClient(app)
    r = client.get("/api/v1/events/timeline", headers=_auth(user))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert isinstance(data, list), f"无参时必须仍是数组，实际 {type(data).__name__}: {data}"
    ids = {e["id"] for e in data}
    assert {str(e.id) for e in created} <= ids


def test_timeline_cursor_pagination_no_gap_no_duplicate(db_user):
    """B-7：游标翻页覆盖全量、不重复、不遗漏（含 start_time 为 NULL 的事件）

    顺序口径：`start_time DESC NULLS FIRST, id DESC`（与全量路径 `start_time DESC` 同序）。
    特意构造 1 条 start_time=None 的事件：它是 NULL 段，翻页时既不能丢也不能重复出现。
    """
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    base = datetime.now(UTC)
    created = _make_events(
        db,
        user,
        [None, base, base - timedelta(hours=1), base - timedelta(days=2)],
    )
    expected = {str(e.id) for e in created}

    client = TestClient(app)
    headers = _auth(user)

    seen: list[str] = []
    cursor = ""
    pages = 0
    while True:
        params = {"limit": 2}
        if cursor != "":
            params["cursor"] = cursor
        r = client.get("/api/v1/events/timeline", params=params, headers=headers)
        assert r.status_code == 200, r.text
        page = r.json()["data"]
        assert isinstance(page, dict), f"带 limit 时必须是 Page 对象，实际 {type(page).__name__}"
        assert set(page.keys()) == {"items", "cursor", "has_more"}, page.keys()
        assert len(page["items"]) <= 2
        seen.extend(e["id"] for e in page["items"])
        pages += 1
        if not page["has_more"]:
            assert page["cursor"] is None, "最后一页不应再给游标"
            break
        assert page["cursor"], "has_more=true 时必须给游标，否则客户端无法续页"
        cursor = page["cursor"]
        assert pages < 10, "翻页未收敛（游标未推进）"

    assert len(seen) == len(set(seen)), f"翻页出现重复事件: {seen}"
    assert set(seen) == expected, f"翻页遗漏/多出: 得到 {set(seen)} 期望 {expected}"
    assert pages == 2, f"4 条事件 limit=2 应为 2 页，实际 {pages}"


def test_timeline_invalid_cursor_is_422_not_silent_restart(db_user):
    """B-7：非法游标 → 422 EVENT_007（不静默退回第一页，否则用户以为翻页生效了）"""
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    _make_events(db, user, [datetime.now(UTC)])

    client = TestClient(app)
    r = client.get(
        "/api/v1/events/timeline", params={"limit": 1, "cursor": "not-a-cursor"}, headers=_auth(user)
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "EVENT_007"


# ---------------------------------------------------------------------------
# C-5 · 检索分页（切片窗口语义，假召回）
# ---------------------------------------------------------------------------


def _fake_hits(n: int) -> list[dict]:
    return [
        {
            "content_id": f"rag-{i:03d}",
            "text": f"第{i}条记忆",
            "score": round(1.0 - i / 100.0, 4),
            "dense_score": 0.9,
            "sparse_score": 0.1,
        }
        for i in range(n)
    ]


@pytest.fixture()
def fake_recall(monkeypatch):
    """把召回换成固定 5 条（不碰 Qdrant/BGE-M3/reranker）——本条只验 offset 窗口切片"""
    import app.services.rag as rag

    hits = _fake_hits(5)

    class _Store:
        def search(self, dense, sparse, filters=None, limit=50, collection=None):
            return [dict(h) for h in hits]

        def search_image(self, vec, filters=None, limit=50, collection=None):
            return [dict(h) for h in hits]

    monkeypatch.setattr(rag, "_rewrite_query", lambda q: (q.q, {}, set()))
    monkeypatch.setattr(rag, "encode_query", lambda text: ([0.0], [0.0]))
    monkeypatch.setattr(rag, "get_store", _Store)
    monkeypatch.setattr(rag, "rerank_auto_enabled", _const_false)
    return hits


def test_search_offset_slices_ranked_results(fake_recall):
    """C-5：offset 在**已排序结果**上切片；total 是候选条数（不是本页条数）"""
    from app.schemas.search import SearchQuery
    from app.services.rag import search

    page1 = search(SearchQuery(q="测试查询", limit=2, offset=0), db=None, user_id=None)
    assert [h.content_id for h in page1.hits] == ["rag-000", "rag-001"]
    assert page1.total == 2, "offset=0 时窗口=limit，候选条数与改造前一致（不静默扩大取数）"
    assert page1.has_more is True

    page2 = search(SearchQuery(q="测试查询", limit=2, offset=2), db=None, user_id=None)
    assert [h.content_id for h in page2.hits] == ["rag-002", "rag-003"]
    assert page2.total == 4, "窗口 = offset+limit，total 必须随之推进（否则客户端无法判断还有没有）"
    assert page2.has_more is True

    page3 = search(SearchQuery(q="测试查询", limit=2, offset=4), db=None, user_id=None)
    assert [h.content_id for h in page3.hits] == ["rag-004"]
    assert page3.total == 5, "候选池只有 5 条：total 应为真实候选数，不是窗口上限 6"
    assert page3.has_more is False, "候选池已见底 ⇒ has_more=false"

    beyond = search(SearchQuery(q="测试查询", limit=2, offset=10), db=None, user_id=None)
    assert beyond.hits == [], "翻过头返回空页（不是报错）"
    assert beyond.total == 5, "翻过头也要能看出总数（否则无法区分「到底了」和「没搜到」）"


def test_search_offset_beyond_recall_window_is_empty(fake_recall):
    """C-5 边界：offset 超过召回窗口时返回空页（窗口就是可翻深度，如实呈现）"""
    from app.schemas.search import SearchQuery
    from app.services.rag import search

    res = search(SearchQuery(q="测试查询", limit=1, offset=100), db=None, user_id=None)
    assert res.hits == []
    assert res.total == 5


# ---------------------------------------------------------------------------
# D-11 · 对话历史分页
# ---------------------------------------------------------------------------


def _make_chat(db, user, conversation_id: str, count: int) -> list:
    from app.db.models import ChatMessage

    base = datetime.now(UTC) - timedelta(minutes=count)
    rows = []
    for i in range(count):
        row = ChatMessage(
            user_id=str(user.id),
            conversation_id=conversation_id,
            role="user" if i % 2 == 0 else "assistant",
            content=f"消息{i}",
            kind="user" if i % 2 == 0 else "bubble",
            created_at=base + timedelta(minutes=i),
        )
        db.add(row)
        rows.append(row)
    db.commit()
    return rows


def test_chat_history_without_params_returns_all(db_user):
    """D-11 向后兼容：不传 limit/cursor ⇒ 全量（老客户端行为不变）+ 分页字段有确定值"""
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    conv = str(uuid.uuid4())
    _make_chat(db, user, conv, 5)

    client = TestClient(app)
    r = client.get(f"/api/v1/chat/conversations/{conv}/messages", headers=_auth(user))
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert len(data["messages"]) == 5
    assert data["has_more"] is False
    assert data["cursor"] is None
    assert data["limit"] is None


def test_chat_history_pages_backwards_in_time_order(db_user):
    """D-11：先给最近 N 条，cursor 往更早翻；**每页出参仍按时间正序**"""
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    conv = str(uuid.uuid4())
    _make_chat(db, user, conv, 5)

    client = TestClient(app)
    headers = _auth(user)

    seen: list[str] = []
    cursor = ""
    while True:
        params = {"limit": 2}
        if cursor != "":
            params["cursor"] = cursor
        r = client.get(
            f"/api/v1/chat/conversations/{conv}/messages", params=params, headers=headers
        )
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        texts = [m["text"] for m in data["messages"]]
        assert texts == sorted(texts, key=lambda t: int(t.replace("消息", ""))), (
            f"单页内必须时间正序（客户端直接渲染，不做二次排序）: {texts}"
        )
        assert data["limit"] == 2
        seen = texts + seen  # 往更早翻 ⇒ 每页拼到最前面
        if not data["has_more"]:
            assert data["cursor"] is None
            break
        assert data["cursor"], "has_more=true 必须给游标"
        cursor = data["cursor"]
        assert len(seen) < 10, "翻页未收敛"

    assert seen == [f"消息{i}" for i in range(5)], f"翻页顺序/内容不对: {seen}"


def test_chat_history_invalid_cursor_is_422(db_user):
    """D-11：非法游标 → 422 CHAT_005（不能静默退化成"最近一页"）"""
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    conv = str(uuid.uuid4())
    _make_chat(db, user, conv, 2)

    client = TestClient(app)
    r = client.get(
        f"/api/v1/chat/conversations/{conv}/messages",
        params={"limit": 1, "cursor": "bogus"},
        headers=_auth(user),
    )
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "CHAT_005"


# ---------------------------------------------------------------------------
# F-6 · 画像重建不再静默截断
# ---------------------------------------------------------------------------


def test_profile_rebuild_processes_beyond_batch_size(db_user, monkeypatch):
    """F-6：内容数 > 批大小 ⇒ 必须全部处理（原实现 `.limit(500)` 静默只处理前 500 条）

    断言三件事，缺一条都可能是"看起来成功"的假绿：
      1. total  = 真实总数（count），不是批大小；
      2. processed == 真实总数（没有静默丢弃）；
      3. applied == 真实总数（不是"取了但没标注"）。
    """
    from app.db.models import Content
    from app.services import profile_rebuild as pr

    db, user = db_user
    now = datetime.now(UTC)
    for i in range(7):
        db.add(
            Content(
                id=str(uuid.uuid4()),
                user_id=user.id,
                content_type="text",
                text=f"重建画像测试-{i}",
                status="done",
                source="app",
                taken_at=now - timedelta(minutes=i),
            )
        )
    db.commit()

    annotated: list[str] = []

    def _fake_annotate(_db, content):
        annotated.append(str(content.id))
        return {"applied": [f"dim-{content.id}"], "pooled": []}

    monkeypatch.setattr(pr, "annotate_content", _fake_annotate)
    monkeypatch.setattr(pr, "llm_available", lambda: False)

    # limit=3 ⇒ 三批（3 + 3 + 1）：若仍把 limit 当总量上限，processed 会停在 3
    summary = pr.rebuild_user_profile(str(user.id), limit=3)

    assert summary["total"] == 7, f"total 应为真实总数 7: {summary}"
    assert summary["processed"] == 7, f"processed 应覆盖全部 7 条（不得静默截断）: {summary}"
    assert summary["applied"] == 7, f"applied 应为 7: {summary}"
    assert summary["batches"] == 3, f"批次数应为 3: {summary}"
    assert len(annotated) == 7
