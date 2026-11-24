"""B5 merge: POST /api/v1/memory/agent writes canonical memory under B6 consent gate."""
from __future__ import annotations

import pytest
from app.db.models.memory import MemoryEntry
from app.main import app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration

AGENT_TOKEN = "test-agent-token-b5"  # noqa: S105 - test stub


@pytest.fixture()
def ctx(db_user, monkeypatch):
    db, user = db_user
    uid = str(user.id)
    monkeypatch.setenv("AGENT_SERVICE_TOKEN", AGENT_TOKEN)
    from app.api import deps as deps_mod
    monkeypatch.setattr(deps_mod.settings, "agent_service_token", AGENT_TOKEN, raising=False)
    return db, user, uid


def _client(user, token=AGENT_TOKEN):
    c = TestClient(app)
    c.headers.update({"X-Agent-Token": token, "X-On-Behalf-Of": str(user.id)})
    return c


def _payload(eid="m_abc123abcdef", text="合并记忆"):
    return {
        "entry_id": eid, "person_id": "p1", "text": text,
        "evidence": [{"type": "agent_evidence", "id": "e1"}],
    }


def test_write_is_on_by_default_without_consent(ctx):
    """写自己的记忆是基础功能：无需授权、默认开（用户拍板 2026-11-24）。"""
    db, user, uid = ctx
    c = _client(user)
    r = c.post("/api/v1/memory/agent", json=_payload("m_111111111111"))
    assert r.status_code == 200, r.text[:200]
    assert db.get(MemoryEntry, "m_111111111111") is not None


def test_write_persists_canonical_row(ctx):
    db, user, uid = ctx
    r = _client(user).post("/api/v1/memory/agent", json=_payload("m_222222222222", "授权后写入"))
    assert r.status_code == 200, r.text[:200]
    row = db.get(MemoryEntry, "m_222222222222")
    assert row is not None and row.text == "授权后写入"
    assert str(row.user_id) == uid
    assert row.created_by == "agent_merge"


def test_write_is_idempotent_by_entry_id(ctx):
    db, user, uid = ctx
    c = _client(user)
    first = c.post("/api/v1/memory/agent", json=_payload("m_333333333333"))
    second = c.post("/api/v1/memory/agent", json=_payload("m_333333333333", "不同正文"))
    assert first.status_code == second.status_code == 200
    assert second.json()["data"]["idempotent"] is True
    row = db.get(MemoryEntry, "m_333333333333")
    assert row.text == "合并记忆", "重试不得覆盖已有记忆"


def test_write_rejects_empty_evidence(ctx):
    db, user, uid = ctx
    payload = _payload("m_444444444444")
    payload["evidence"] = []
    r = _client(user).post("/api/v1/memory/agent", json=payload)
    assert r.status_code == 422  # schema min_length=1


def test_write_rejects_malformed_entry_id(ctx):
    db, user, uid = ctx
    r = _client(user).post("/api/v1/memory/agent", json=_payload("bad-id"))
    assert r.status_code == 422


def test_write_rejects_unknown_kind(ctx):
    db, user, uid = ctx
    payload = _payload("m_555555555555")
    payload["kind"] = "ai_inferred"
    r = _client(user).post("/api/v1/memory/agent", json=payload)
    assert r.status_code == 422


def test_write_audits_each_success(ctx):
    db, user, uid = ctx
    c = _client(user)
    c.post("/api/v1/memory/agent", json=_payload("m_666666666666"))
    from app.services.audit import list_audit
    rows = list_audit(db, uid, action="memory_written")
    assert any((r.get("target") if isinstance(r, dict) else r.target) == "m_666666666666" for r in rows)


def test_cross_user_entry_id_cannot_overwrite(ctx, make_user):
    db, user, uid = ctx
    other = make_user(db, "b5-write-other")
    try:
        c = _client(user)
        r = c.post("/api/v1/memory/agent", json=_payload("m_777777777777", "属于user1"))
        assert r.status_code == 200
        # B 用户拿同一 entry_id 请求 → 幂等返回，不得改写 A 的行
        other_client = TestClient(app)
        other_client.headers.update({"X-Agent-Token": AGENT_TOKEN, "X-On-Behalf-Of": str(other.id)})
        r2 = other_client.post("/api/v1/memory/agent", json=_payload("m_777777777777", "属于user2"))
        assert r2.status_code == 200
        row = db.get(MemoryEntry, "m_777777777777")
        assert row.text == "属于user1" and str(row.user_id) == uid
    finally:
        db.delete(other)
        db.commit()


def test_purge_deletes_canonical_row(ctx):
    """B5 合并：purge 回写后端，避免下次 hydrate 复活已删记忆。"""
    db, user, uid = ctx
    c = _client(user)
    eid = "m_888888888888"
    assert c.post("/api/v1/memory/agent", json=_payload(eid)).status_code == 200
    assert db.get(MemoryEntry, eid) is not None
    r = c.delete(f"/api/v1/memory/agent/{eid}")
    assert r.status_code == 200 and r.json()["data"]["deleted"] is True
    assert db.get(MemoryEntry, eid) is None


def test_delete_is_on_by_default_without_consent(ctx):
    """删自己的记忆与写入同属基础功能，默认开。"""
    db, user, uid = ctx
    r = _client(user).delete("/api/v1/memory/agent/m_999999999999")
    assert r.status_code == 200


def test_delete_foreign_entry_is_noop(ctx, make_user):
    db, user, uid = ctx
    other = make_user(db, "b5-del-other")
    try:
        eid = "m_aaaaaaaaaaaa"
        _client(user).post("/api/v1/memory/agent", json=_payload(eid, "属于user1"))
        other_client = TestClient(app)
        other_client.headers.update({"X-Agent-Token": AGENT_TOKEN, "X-On-Behalf-Of": str(other.id)})
        r = other_client.delete(f"/api/v1/memory/agent/{eid}")
        assert r.status_code == 200 and r.json()["data"]["deleted"] is False
        assert db.get(MemoryEntry, eid) is not None, "不能删除他人的记忆"
    finally:
        db.delete(other)
        db.commit()
