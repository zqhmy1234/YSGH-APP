"""画像重建服务测试（P0-3 · 2026-10-28）。"""
from __future__ import annotations

import uuid

import pytest
from app.db.models import Content
from app.services import profile_rebuild

pytestmark = pytest.mark.integration


def test_rebuild_existing_memories_is_idempotent(db_user, monkeypatch):
    db, user = db_user
    marker = f"画像重建{uuid.uuid4().hex[:8]}"
    rows = [
        Content(
            user_id=user.id,
            content_type="text",
            text=f"{marker} 第一条",
            status="done",
            source="app",
        ),
        Content(
            user_id=user.id,
            content_type="voice",
            text=f"{marker} 第二条",
            status="done",
            source="app",
        ),
    ]
    db.add_all(rows)
    db.commit()

    calls: list[str] = []

    def fake_annotate(_db, content):
        calls.append(str(content.id))
        return {"applied": [{"dimension": "relation_role"}], "pooled": []}

    monkeypatch.setattr(profile_rebuild, "annotate_content", fake_annotate)

    first = profile_rebuild.rebuild_user_profile(str(user.id))
    assert first["total"] == 2
    assert first["processed"] == 2
    assert first["applied"] == 2
    assert first["failed"] == 0
    assert sorted(calls) == sorted(str(c.id) for c in rows)

    db.expire_all()
    for content in rows:
        fresh = db.get(Content, content.id)
        assert fresh is not None
        assert (fresh.extra or {}).get("profile_rebuilt_at")

    calls.clear()
    second = profile_rebuild.rebuild_user_profile(str(user.id))
    assert second["total"] == 2
    assert second["processed"] == 2
    assert second["skipped"] == 2
    assert second["applied"] == 0
    assert calls == []


def test_rebuild_isolates_single_content_failure(db_user, monkeypatch):
    db, user = db_user
    marker = f"画像失败隔离{uuid.uuid4().hex[:8]}"
    ok = Content(
        user_id=user.id,
        content_type="text",
        text=f"{marker} 正常",
        status="done",
        source="app",
    )
    bad = Content(
        user_id=user.id,
        content_type="text",
        text=f"{marker} 异常",
        status="done",
        source="app",
    )
    db.add_all([ok, bad])
    db.commit()

    def fake_annotate(_db, content):
        if content.id == bad.id:
            raise RuntimeError("probe failure")
        return {"applied": [{"dimension": "relation_role"}], "pooled": []}

    monkeypatch.setattr(profile_rebuild, "annotate_content", fake_annotate)
    summary = profile_rebuild.rebuild_user_profile(str(user.id))

    assert summary["processed"] == 2
    assert summary["applied"] == 1
    assert summary["failed"] == 1

    db.expire_all()
    good = db.get(Content, ok.id)
    failed = db.get(Content, bad.id)
    assert good is not None and (good.extra or {}).get("profile_rebuilt_at")
    assert failed is not None and not (failed.extra or {}).get("profile_rebuilt_at")
