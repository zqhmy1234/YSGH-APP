"""检索过滤器契约单一来源（D05-3 · 功能修复波 域⑤）回归测试。

缺陷原貌（P0 级风险）：过滤器翻译有**两套独立实现**，各自 `if/elif` 链、各自维护
键集 —— `vector_store._to_filter`（Qdrant payload）链尾**无 else ⇒ 未知键静默丢弃**；
`rag.pg_fallback` 逐个 `filters.get(...)` 同样静默忽略。任何**隔离类键**（如 `user_id`）
被丢弃 ⇒ 过滤条件不生效 ⇒ **跨用户召回**，且无任何日志/异常。

修复后口径：键集唯一来源 `services/search_filters.FILTER_KEYS`；未知键
**抛 `UnknownFilterKey`**（fail-closed）；两侧覆盖声明经 `assert_full_coverage` 校验。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.services.search_filters import (
    FILTER_KEYS,
    HANDLED_BY_PG,
    HANDLED_BY_QDRANT,
    ISOLATION_KEYS,
    UnknownFilterKey,
    assert_full_coverage,
    normalize_filters,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# 1. 契约本体：未知键 fail-closed / 覆盖声明完整
# ---------------------------------------------------------------------------


def test_filter_keys_are_single_source_and_complete():
    """键集唯一来源且两侧覆盖声明一致（漏接任一侧即本用例红）。"""
    assert set(HANDLED_BY_QDRANT) == set(FILTER_KEYS)
    assert set(HANDLED_BY_PG) == set(FILTER_KEYS)
    assert "user_id" in ISOLATION_KEYS
    assert_full_coverage("probe", set(FILTER_KEYS))  # 不抛即覆盖齐备


def test_normalize_rejects_unknown_key_and_drops_empty():
    """未知键抛错（绝不静默丢弃）；空值键丢弃；合法键原样保留。"""
    with pytest.raises(UnknownFilterKey) as exc:
        normalize_filters({"user_ids": "u1"})  # 拼错一个字符（隔离键）必须炸
    assert "user_ids" in str(exc.value)

    assert normalize_filters({"user_id": "u1", "content_types": None}) == {"user_id": "u1"}
    assert normalize_filters(None) == {}
    assert normalize_filters({}) == {}


def test_assert_full_coverage_detects_missing_key():
    """覆盖自检有鉴别力：漏一个键即报错并点名。"""
    with pytest.raises(AssertionError) as exc:
        assert_full_coverage("probe", set(FILTER_KEYS) - {"user_id"})
    assert "user_id" in str(exc.value)


# ---------------------------------------------------------------------------
# 2. Qdrant 侧：未知键不再静默、隔离键真生成条件
# ---------------------------------------------------------------------------


def test_to_filter_raises_on_unknown_key():
    from app.services.vector_store import VectorStore

    with pytest.raises(UnknownFilterKey):
        VectorStore._to_filter({"user_ids": "u1"})


def test_to_filter_isolates_by_user_id():
    """**隔离回归**：user_id 必须真的变成一条 FieldCondition（而非被链尾丢掉）。"""
    from app.services.vector_store import VectorStore

    f = VectorStore._to_filter({"user_id": "u-1"})
    assert f is not None and f.must
    cond = f.must[0]
    assert cond.key == "user_id"
    assert cond.match.value == "u-1"


@pytest.mark.parametrize("key", FILTER_KEYS)
def test_to_filter_handles_every_declared_key(key):
    """每个声明键都必须被 `_to_filter` 真正消费（否则 = 声明与实现漂移）。"""
    from app.services.vector_store import VectorStore

    values = {
        "content_types": ["photo"],
        "time_from": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "time_to": datetime(2026, 2, 1, tzinfo=timezone.utc),
        "place": "苏州",
        "tag": "小张",
        "content_class": "travel",
        "user_id": "u-1",
    }
    f = VectorStore._to_filter({key: values[key]})
    assert f is not None and f.must, f"键 {key} 未生成任何条件（静默丢弃）"


def test_to_filter_empty_returns_none():
    from app.services.vector_store import VectorStore

    assert VectorStore._to_filter(None) is None
    assert VectorStore._to_filter({}) is None
    assert VectorStore._to_filter({"place": None}) is None


# ---------------------------------------------------------------------------
# 3. PG 兜底侧：未知键同样抛错；user_id 生效
# ---------------------------------------------------------------------------


def test_pg_fallback_raises_on_unknown_key():
    from app.schemas.search import SearchQuery
    from app.services.rag.pg_fallback import _pg_fallback_search

    with pytest.raises(UnknownFilterKey):
        _pg_fallback_search(
            SearchQuery(q="苏州"), "苏州", {"user_ids": "u1"}, db=object(), user_id="u1", limit=5
        )


def test_pg_fallback_applies_filter_user_id(db_user, make_user, cleanup_user):
    """PG 兜底的隔离值以**过滤器**为准（此前只看入参 ⇒ 过滤器里的 user_id 被忽略）。"""
    from app.db.models import Content
    from app.schemas.search import SearchQuery
    from app.services.rag.pg_fallback import _pg_fallback_search

    db, user = db_user
    other = make_user(db, "sf-other")
    other_id = other.id
    word = f"过滤键探针{uuid.uuid4().hex[:6]}"
    mine = Content(user_id=user.id, content_type="text", text=f"{word} 我的", source="app", status="confirmed")
    theirs = Content(user_id=other_id, content_type="text", text=f"{word} 他人的", source="app", status="confirmed")
    db.add_all([mine, theirs])
    db.commit()
    try:
        # 入参 uid 与过滤器 uid 一致 → 只召回自己的
        hits = _pg_fallback_search(
            SearchQuery(q=word), word, {"user_id": str(user.id)}, db=db, user_id=str(user.id), limit=10
        )
        assert [h["content_id"] for h in hits] == [str(mine.id)]
        # 过滤器指向他人 → 只能召回他人的那条（绝不因"入参不同"而忽略过滤器）
        hits2 = _pg_fallback_search(
            SearchQuery(q=word), word, {"user_id": str(other_id)}, db=db, user_id=str(user.id), limit=10
        )
        assert [h["content_id"] for h in hits2] == [str(theirs.id)]
    finally:
        from sqlalchemy import delete as sa_delete

        db.execute(sa_delete(Content).where(Content.id.in_([mine.id, theirs.id])))
        db.commit()
        cleanup_user(db, other.id)


def test_pg_fallback_time_and_type_filters(db_user):
    """时间/类型过滤在 PG 兜底真实生效（两实现同键同语义的最小对拍）。"""
    from app.db.models import Content
    from app.schemas.search import SearchQuery
    from app.services.rag.pg_fallback import _pg_fallback_search

    db, user = db_user
    word = f"对拍探针{uuid.uuid4().hex[:6]}"
    now = datetime.now(timezone.utc)
    old = Content(
        user_id=user.id, content_type="text", text=f"{word} 旧", source="app",
        status="confirmed", taken_at=now - timedelta(days=30),
    )
    new = Content(
        user_id=user.id, content_type="voice", text=f"{word} 新", source="app",
        status="confirmed", taken_at=now,
    )
    db.add_all([old, new])
    db.commit()
    try:
        hits = _pg_fallback_search(
            SearchQuery(q=word), word,
            {"user_id": str(user.id), "time_from": now - timedelta(days=1)},
            db=db, user_id=str(user.id), limit=10,
        )
        assert [h["content_id"] for h in hits] == [str(new.id)]

        hits2 = _pg_fallback_search(
            SearchQuery(q=word), word,
            {"user_id": str(user.id), "content_types": ["text"]},
            db=db, user_id=str(user.id), limit=10,
        )
        assert [h["content_id"] for h in hits2] == [str(old.id)]
    finally:
        from sqlalchemy import delete as sa_delete

        db.execute(sa_delete(Content).where(Content.id.in_([old.id, new.id])))
        db.commit()
