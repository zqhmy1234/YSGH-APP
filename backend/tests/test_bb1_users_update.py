"""BB1：GET /users/me 手机号掩码下发 + PATCH /contents/{id} 备注（2026-09-08）

前置：本地 PostgreSQL yishu 隔离库 + Redis（RQ）
"""
import uuid

import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    return TestClient(app)


# ═══════════ GET /api/v1/users/me（BB1-B4：手机号服务端掩码）═══════════


def test_users_me_masks_phone(client, auth_headers, cleanup_user):
    """11 位手机号 → 138****XXXX 掩码；响应体绝不含完整号码"""
    from app.db.models import User
    from app.db.session import SessionLocal

    user_id, headers = auth_headers("bb1a")
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        assert u is not None
        u.phone = "138" + uuid.uuid4().hex[:8]  # 唯一 11 位号（users_phone_key 约束）
        full_phone = u.phone
        db.commit()

        r = client.get("/api/v1/users/me", headers=headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["id"] == user_id
        assert data["phone"] == full_phone[:3] + "****" + full_phone[7:]
        # 安全断言：完整手机号绝不出网（掩码在服务端做）
        assert full_phone not in r.text
        assert "nickname" in data
        assert "avatar" in data
    finally:
        cleanup_user(db, user_id)
        db.close()


def test_users_me_phone_none_when_absent(client, auth_headers, cleanup_user):
    """未绑定手机号（null/非 11 位）→ phone=null，不造假"""
    from app.db.models import User
    from app.db.session import SessionLocal

    user_id, headers = auth_headers("bb1b")
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        assert u is not None
        u.phone = None
        db.commit()

        r = client.get("/api/v1/users/me", headers=headers)
        assert r.status_code == 200
        assert r.json()["data"]["phone"] is None
    finally:
        cleanup_user(db, user_id)
        db.close()


def test_users_me_non11_phone_masked_to_none(client, auth_headers, cleanup_user):
    """库中非 11 位（脏数据/测试号）→ 不下发原文，一律 null"""
    from app.db.models import User
    from app.db.session import SessionLocal

    user_id, headers = auth_headers("bb1g")
    db = SessionLocal()
    try:
        u = db.get(User, user_id)
        assert u is not None
        u.phone = f"bb1g-{uuid.uuid4().hex[:4]}"  # 9 位：非 11 位脏数据形态
        db.commit()

        r = client.get("/api/v1/users/me", headers=headers)
        assert r.status_code == 200
        data = r.json()["data"]
        assert data["phone"] is None
        assert u.phone not in r.text
    finally:
        cleanup_user(db, user_id)
        db.close()


def test_users_me_requires_auth(client):
    """无 token → 401"""
    r = client.get("/api/v1/users/me")
    assert r.status_code == 401


# ═══════════ PATCH /api/v1/contents/{id}（BB1：仅 remark 单字段）═══════════


def _create_text_content(client, headers) -> str:
    r = client.post(
        "/api/v1/contents",
        json={"content_type": "text", "text": f"备注测试-{uuid.uuid4().hex[:6]}", "source": "app"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def test_update_remark_roundtrip(client, auth_headers):
    """更新成功 + 回读一致 + remark 可置 null 清除"""
    _, headers = auth_headers("bb1c")
    cid = _create_text_content(client, headers)

    r = client.patch(f"/api/v1/contents/{cid}", json={"remark": "这是用户备注"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["remark"] == "这是用户备注"
    assert r.json()["data"]["id"] == cid

    # 回读（列表页 content_id 精确查询）
    r2 = client.get(f"/api/v1/contents?content_id={cid}", headers=headers)
    assert r2.status_code == 200
    items = r2.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["remark"] == "这是用户备注"

    # 清除备注（null）
    r3 = client.patch(f"/api/v1/contents/{cid}", json={"remark": None}, headers=headers)
    assert r3.status_code == 200
    assert r3.json()["data"]["remark"] is None


def test_update_remark_other_user_404(client, auth_headers):
    """他人内容 → 404 CONTENT_010（IDOR 不泄露存在性）"""
    _, owner = auth_headers("bb1d")
    _, other = auth_headers("bb1e")
    cid = _create_text_content(client, owner)

    r = client.patch(f"/api/v1/contents/{cid}", json={"remark": "越权改"}, headers=other)
    assert r.status_code == 404, r.text


def test_update_remark_extra_field_422(client, auth_headers):
    """越权字段（status/user_id 等）→ 422 显式拒绝，防越权改"""
    _, headers = auth_headers("bb1f")
    cid = _create_text_content(client, headers)

    r = client.patch(
        f"/api/v1/contents/{cid}",
        json={"remark": "x", "status": "done"},
        headers=headers,
    )
    assert r.status_code == 422, r.text
    # status 未被改动
    r2 = client.get(f"/api/v1/contents?content_id={cid}", headers=headers)
    assert r2.json()["data"]["items"][0]["status"] == "processing"


def test_update_remark_not_found_404(client, auth_headers):
    """不存在的内容 / 畸形 ID → 404"""
    _, headers = auth_headers("bb1h")
    r = client.patch(f"/api/v1/contents/{uuid.uuid4()}", json={"remark": "x"}, headers=headers)
    assert r.status_code == 404
    r2 = client.patch("/api/v1/contents/not-a-uuid", json={"remark": "x"}, headers=headers)
    assert r2.status_code == 404
