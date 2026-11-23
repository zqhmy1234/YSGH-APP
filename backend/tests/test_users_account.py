"""账号注销/恢复 HTTP 契约（30 天窗口）。

覆盖：
  * DELETE /users/me 必须显式 confirm；软删内容、关闭普通业务接口，但 /me 可读；
  * POST /users/me/restore 在窗口内恢复内容与账号；
  * 窗口外恢复 → 410 USER_002，普通接口仍 401。
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.db.models import Content, User
from app.db.models.memory import MemoryBatch
from app.db.session import SessionLocal
from sqlalchemy import delete, select


@pytest.fixture()
def account_user(client, auth_headers, cleanup_user):
    uid, headers = auth_headers("acct")
    yield uid, headers
    db = SessionLocal()
    cleanup_user(db, uid)
    batch_hash = hashlib.sha256(f"account-purge:{uid}".encode()).hexdigest()
    db.execute(delete(MemoryBatch).where(MemoryBatch.batch_hash == batch_hash))
    db.execute(delete(User).where(User.id == uid))
    db.commit()
    db.close()


def _make_content(user_id: str) -> str:
    db = SessionLocal()
    row = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type="text",
        text="账号注销测试内容",
        status="ready",
        source="app",
    )
    db.add(row)
    db.commit()
    cid = str(row.id)
    db.close()
    return cid


def _content_deleted_at(content_id: str) -> datetime | None:
    db = SessionLocal()
    row = db.execute(select(Content).where(Content.id == content_id)).scalar_one()
    value = row.deleted_at
    db.close()
    return value


def test_delete_soft_deletes_content_allows_me_and_restores(client, account_user):
    uid, headers = account_user
    cid = _make_content(uid)

    assert client.request(
        "DELETE", "/api/v1/users/me", headers=headers, json={}
    ).status_code == 422

    r = client.request(
        "DELETE", "/api/v1/users/me", headers=headers, json={"confirm": "delete"}
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["account_status"] == "pending_deletion"
    assert data["undo_until"]
    assert 29 <= data["days_left"] <= 30
    assert _content_deleted_at(cid) is not None

    me = client.get("/api/v1/users/me", headers=headers)
    assert me.status_code == 200, me.text
    assert me.json()["data"]["account_status"] == "pending_deletion"

    # 普通业务接口必须被账号状态拦下，不能继续写/读
    assert client.get("/api/v1/contents", headers=headers).status_code == 401

    # 幂等：窗口内重复注销不应重置 30 天或失败
    again = client.request(
        "DELETE", "/api/v1/users/me", headers=headers, json={"confirm": "delete"}
    )
    assert again.status_code == 200
    assert again.json()["data"]["account_status"] == "pending_deletion"

    restore = client.post("/api/v1/users/me/restore", headers=headers)
    assert restore.status_code == 200, restore.text
    assert restore.json()["data"]["account_status"] == "active"
    assert _content_deleted_at(cid) is None
    assert client.get("/api/v1/contents", headers=headers).status_code == 200


def test_restore_after_window_returns_410(client, account_user):
    uid, headers = account_user
    r = client.request(
        "DELETE", "/api/v1/users/me", headers=headers, json={"confirm": "delete"}
    )
    assert r.status_code == 200, r.text

    db = SessionLocal()
    user = db.get(User, uid)
    user.deleted_at = datetime.now(UTC) - timedelta(days=31)
    db.commit()
    db.close()

    restore = client.post("/api/v1/users/me/restore", headers=headers)
    assert restore.status_code == 410
    assert restore.json()["code"] == "USER_002"
    assert client.get("/api/v1/users/me", headers=headers).status_code == 410


def test_restore_when_active_returns_409(client, account_user):
    _, headers = account_user
    r = client.post("/api/v1/users/me/restore", headers=headers)
    assert r.status_code == 409
    assert r.json()["code"] == "USER_003"


def test_purge_due_account_is_hard_delete_and_idempotent(client, account_user):
    from app.domain.memory.purge import purge_due_account

    uid, headers = account_user
    client.request("DELETE", "/api/v1/users/me", headers=headers, json={"confirm": "delete"})
    db = SessionLocal()
    user = db.get(User, uid)
    user.deleted_at = datetime.now(UTC) - timedelta(days=31)
    db.commit()

    now = datetime.now(UTC)
    first = purge_due_account(db, uid, now=now)
    assert first.status == "done", first.warnings
    assert first.layers.get("account") == "purged"

    second = purge_due_account(db, uid, now=now)
    assert second.status == "done"
    assert "already_purged" in second.warnings
    db.close()


def test_run_account_purges_selected_due_user(client, account_user):
    from app.workers.cleanup_job import run_account_purges

    uid, headers = account_user
    client.request("DELETE", "/api/v1/users/me", headers=headers, json={"confirm": "delete"})
    db = SessionLocal()
    user = db.get(User, uid)
    user.deleted_at = datetime.now(UTC) - timedelta(days=31)
    db.commit()
    db.close()

    first = run_account_purges(older_than_days=30, limit=50)
    assert first["scanned"] >= 1
    assert first["purged"] >= 1
    assert first["failed"] == 0

    second = run_account_purges(older_than_days=30, limit=50)
    assert second["already_purged"] >= 1
    assert second["failed"] == 0
