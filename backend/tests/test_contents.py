"""内容 API 测试（对齐测试清单 API-002/005：上传主链路 + 参数边界 + 去重）

前置：本地 PostgreSQL yishu 隔离库 + Redis（RQ）
"""
import uuid

import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def auth_headers(client):
    """登录拿 token（微信 mock）"""
    r = client.post(
        "/api/v1/auth/wechat",
        json={"code": f"ct-{uuid.uuid4().hex[:8]}", "device_id": "ct-dev"},
    )
    assert r.status_code == 200
    token = r.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _current_user_id(headers) -> str:
    """从 access token 解析当前用户 id（cos_key 前缀校验需要真实用户前缀）"""
    from app.core.security import decode_token

    token = headers["Authorization"].split(" ", 1)[1]
    return decode_token(token)["sub"]


def _seed_object(cos_key: str) -> None:
    from app.services.external.storage import get_storage_backend

    get_storage_backend().put_object(cos_key, b"fake-object-bytes")


def test_create_text_content(client, auth_headers):
    """文字碎片入库（F2）"""
    r = client.post(
        "/api/v1/contents",
        json={"content_type": "text", "text": "明天记得买咖啡豆", "source": "app"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["content_type"] == "text"
    assert data["status"] == "processing"  # 异步 AI 管线前状态


def test_create_photo_content(client, auth_headers):
    """照片入库（F1）——cos_key 需为本用户前缀且对象存在（TD-P3 M4 校验）"""
    user_id = _current_user_id(auth_headers)
    cos_key = f"photos/{user_id}/202608/ok_{uuid.uuid4().hex[:8]}.jpg"
    _seed_object(cos_key)
    r = client.post(
        "/api/v1/contents",
        json={"content_type": "photo", "cos_key": cos_key, "source": "app"},
        headers=auth_headers,
    )
    assert r.status_code == 200
    assert r.json()["data"]["content_type"] == "photo"


def test_create_invalid_type(client, auth_headers):
    """非法 content_type → 422（API-005 参数边界）"""
    r = client.post(
        "/api/v1/contents",
        json={"content_type": "video", "source": "app"},
        headers=auth_headers,
    )
    assert r.status_code == 422


def test_presign_removed(client, auth_headers):
    """2026-08-26 决策：presign 删除（无消费方 + 与 /upload/sts 重叠），STS 直传归口 /upload/sts"""
    # 2026-09-05：DELETE /contents/{content_id}（W2-1）参数路由接管后，
    # POST 到该形状路径返回 405（路径存在但方法不允许）——同样证明 presign 无 POST 端点
    r = client.post("/api/v1/contents/presign", json={}, headers=auth_headers)
    assert r.status_code in (404, 405)


def test_list_contents(client, auth_headers):
    """内容列表分页（API-006）"""
    r = client.get("/api/v1/contents?limit=5", headers=auth_headers)
    assert r.status_code == 200
    assert isinstance(r.json()["data"]["items"], list)


def test_duplicate_hash_rejected(client, auth_headers):
    """同用户同感知哈希 → 409（Q16 去重）——cos_key 需为本用户前缀且对象存在（TD-P3 M4）"""
    user_id = _current_user_id(auth_headers)
    cos_key = f"photos/{user_id}/202608/dup_{uuid.uuid4().hex[:8]}.jpg"
    _seed_object(cos_key)
    payload = {
        "content_type": "photo",
        "perceptual_hash": "hash-dup-001",
        "cos_key": cos_key,
        "source": "app",
    }
    r1 = client.post("/api/v1/contents", json=payload, headers=auth_headers)
    assert r1.status_code == 200
    r2 = client.post("/api/v1/contents", json=payload, headers=auth_headers)
    assert r2.status_code == 409
    assert r2.json()["code"] == "CONTENT_002"


def test_requires_auth(client):
    """未带 token → 401（AUTH-005）"""
    r = client.post("/api/v1/contents", json={"content_type": "text", "text": "x", "source": "app"})
    assert r.status_code == 401


def test_cursor_pagination(client, auth_headers):
    """游标分页：先建 3 条再翻页（API-006）"""
    for i in range(3):
        client.post(
            "/api/v1/contents",
            json={"content_type": "text", "text": f"page-item-{i}", "source": "app"},
            headers=auth_headers,
        )
    r = client.get("/api/v1/contents?limit=2", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data["items"]) == 2
    assert data["has_more"] is True
    assert data["cursor"] is not None

    r2 = client.get(f"/api/v1/contents?limit=2&cursor={data['cursor']}", headers=auth_headers)
    assert r2.status_code == 200


# ---------------------------------------------------------------------------
# H3：create_content cos_key 归属/前缀/存在性校验（原 test_security_p3.py M4 按域迁入）
# ---------------------------------------------------------------------------


def test_create_content_cos_key_cross_user_rejected(client, auth_headers):
    """A 提交 B 前缀的 cos_key → 422 CONTENT_009（跨租户对象拉取被拒）"""
    # auth_headers 只回传 token，两个用户分别登录取真实 user_id
    headers_a = auth_headers
    headers_b = client.post(
        "/api/v1/auth/wechat",
        json={"code": f"ct-b-{uuid.uuid4().hex[:8]}", "device_id": "ct-b-dev"},
    )
    assert headers_b.status_code == 200
    token_b = headers_b.json()["data"]["access_token"]
    from app.core.security import decode_token

    user_b = decode_token(token_b)["sub"]
    body = {
        "content_type": "voice",
        "cos_key": f"voice/{user_b}/202608/victim.wav",
        "source": "app",
    }
    r = client.post("/api/v1/contents", json=body, headers=headers_a)
    assert r.status_code == 422
    assert r.json()["code"] == "CONTENT_009"


def test_create_content_cos_key_missing_object_rejected(client, auth_headers):
    """本用户前缀但对象不存在 → 422 CONTENT_009（任意 key 不触发存储遍历/管线）"""
    user_id = _current_user_id(auth_headers)
    body = {
        "content_type": "photo",
        "cos_key": f"photos/{user_id}/202608/nonexistent.jpg",
        "source": "app",
    }
    r = client.post("/api/v1/contents", json=body, headers=auth_headers)
    assert r.status_code == 422
    assert r.json()["code"] == "CONTENT_009"


def test_create_content_cos_key_valid_passes(client, auth_headers):
    """本用户前缀 + 对象存在 → 正常入库（不误伤合法 voice/photo 回传）"""
    user_id = _current_user_id(auth_headers)
    cos_key = f"voice/{user_id}/202608/ok_{uuid.uuid4().hex[:8]}.wav"
    _seed_object(cos_key)
    body = {
        "content_type": "voice",
        "cos_key": cos_key,
        "source": "app",
        "extra": {"duration_ms": 1000},
    }
    try:
        r = client.post("/api/v1/contents", json=body, headers=auth_headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["content_type"] == "voice"
    finally:
        from app.db.models import Content
        from app.db.session import SessionLocal
        from sqlalchemy import delete as sa_delete

        db = SessionLocal()
        try:
            db.execute(sa_delete(Content).where(Content.user_id == user_id))
            db.commit()
        finally:
            db.close()


# ---------------------------------------------------------------------------
# A9（2026-09-09 缺口收口）：POST /contents voice 单段路径回填 size_bytes
# ---------------------------------------------------------------------------


def _db_row_size_bytes(content_id: str) -> int | None:
    """DB 回查 size_bytes（TestClient 会话与请求会话不同，需独立会话重读）"""
    from app.db.models import Content
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        row = db.get(Content, content_id)
        assert row is not None, "内容行应存在"
        return row.size_bytes
    finally:
        db.close()


def test_create_voice_backfills_size_bytes(client, auth_headers):
    """A9：voice 带 cos_key（字节已在存储）→ 建记录实测回填 size_bytes（契约偏差①收口）"""
    user_id = _current_user_id(auth_headers)
    wav = b"RIFF" + b"\x00" * 100  # 104 字节假对象（存在性校验通过即可，无需合法 WAV）
    cos_key = f"voice/{user_id}/202609/a9_{uuid.uuid4().hex[:8]}.wav"
    from app.services.external.storage import get_storage_backend

    get_storage_backend().put_object(cos_key, wav)
    try:
        r = client.post(
            "/api/v1/contents",
            json={"content_type": "voice", "cos_key": cos_key, "source": "app"},
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        cid = r.json()["data"]["id"]
        # 出参与 DB 双断言：size_bytes = 存储对象实测字节数
        assert r.json()["data"]["size_bytes"] == len(wav), "出参应回显实测大小"
        assert _db_row_size_bytes(cid) == len(wav), "DB 应落 size_bytes"
    finally:
        from app.db.models import Content
        from app.db.session import SessionLocal
        from sqlalchemy import delete as sa_delete

        db = SessionLocal()
        try:
            db.execute(sa_delete(Content).where(Content.user_id == user_id))
            db.commit()
        finally:
            db.close()


def test_create_voice_declared_size_wins(client, auth_headers):
    """A9：客户端显式声明 size_bytes 优先于存储实测（上送 999 → 落 999）"""
    user_id = _current_user_id(auth_headers)
    cos_key = f"voice/{user_id}/202609/a9d_{uuid.uuid4().hex[:8]}.wav"
    from app.services.external.storage import get_storage_backend

    get_storage_backend().put_object(cos_key, b"x" * 50)
    try:
        r = client.post(
            "/api/v1/contents",
            json={
                "content_type": "voice", "cos_key": cos_key, "source": "app",
                "size_bytes": 999,
            },
            headers=auth_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["size_bytes"] == 999, "客户端声明应优先"
    finally:
        from app.db.models import Content
        from app.db.session import SessionLocal
        from sqlalchemy import delete as sa_delete

        db = SessionLocal()
        try:
            db.execute(sa_delete(Content).where(Content.user_id == user_id))
            db.commit()
        finally:
            db.close()


def test_size_probe_failure_degrades_to_none(client, auth_headers, monkeypatch):
    """A9 真实降级路径：存在性校验通过但 get_object 抛错（并发删对象/存储抖动）
    → 仍 200 建记录、size_bytes=None 不阻塞（_resolve_size_bytes except 分支直测）"""
    user_id = _current_user_id(auth_headers)
    cos_key = f"voice/{user_id}/202609/a9r_{uuid.uuid4().hex[:8]}.wav"
    from app.services.external.storage import get_storage_backend

    backend = get_storage_backend()
    backend.put_object(cos_key, b"y" * 64)

    def _boom(_key):
        raise KeyError(_key)  # 模拟 object_exists 后对象被并发删除

    monkeypatch.setattr(backend, "get_object", _boom)
    try:
        r = client.post(
            "/api/v1/contents",
            json={"content_type": "voice", "cos_key": cos_key, "source": "app"},
            headers=auth_headers,
        )
        assert r.status_code == 200, f"降级不得阻塞建记录: {r.text}"
        assert r.json()["data"]["size_bytes"] is None, "降级应为 None"
    finally:
        from app.db.models import Content
        from app.db.session import SessionLocal
        from sqlalchemy import delete as sa_delete

        db = SessionLocal()
        try:
            db.execute(sa_delete(Content).where(Content.user_id == user_id))
            db.commit()
        finally:
            db.close()


# ---------------------------------------------------------------------------
# 详情路由（2026-09-09 缺口收口）：GET /contents/{content_id}
# （契约偏差③收口：此前客户端用 GET /contents?content_id= 列表过滤替代）
# ---------------------------------------------------------------------------


def test_detail_get_by_id(client, auth_headers):
    """详情：本人内容 200 + 全字段出参（text/remark/duration/size_bytes）"""
    user_id = _current_user_id(auth_headers)
    cos_key = f"voice/{user_id}/202609/d1_{uuid.uuid4().hex[:8]}.wav"
    _seed_object(cos_key)
    r = client.post(
        "/api/v1/contents",
        json={
            "content_type": "voice", "cos_key": cos_key, "source": "app",
            "remark": "详情备注", "extra": {"duration_ms": 5000},
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    cid = r.json()["data"]["id"]
    try:
        r2 = client.get(f"/api/v1/contents/{cid}", headers=auth_headers)
        assert r2.status_code == 200, r2.text
        d = r2.json()["data"]
        assert d["id"] == cid, "详情 id 应对应"
        assert d["content_type"] == "voice"
        assert d["remark"] == "详情备注", f"remark 应直出: {d['remark']!r}"
        assert d["duration"] == 5, f"duration 应 5: {d['duration']}"
        assert d["size_bytes"] == len(b"fake-object-bytes"), "size_bytes 应实测回填"
        for field in ("status", "created_at", "taken_at", "tags_json", "ai_description"):
            assert field in d, f"ContentOut 缺字段 {field}"
    finally:
        from app.db.models import Content
        from app.db.session import SessionLocal
        from sqlalchemy import delete as sa_delete

        db = SessionLocal()
        try:
            db.execute(sa_delete(Content).where(Content.user_id == user_id))
            db.commit()
        finally:
            db.close()


def test_detail_idor_and_soft_deleted_404(client, auth_headers):
    """详情越权三态：他人内容 / 软删条目 / 畸形 ID → 全 404 CONTENT_010（IDOR 不区分）"""
    user_id = _current_user_id(auth_headers)
    r = client.post(
        "/api/v1/contents",
        json={"content_type": "text", "text": "详情三态", "source": "app"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    cid = r.json()["data"]["id"]
    try:
        # ① 本人正常 200
        assert client.get(f"/api/v1/contents/{cid}", headers=auth_headers).status_code == 200
        # ② 他人（B 登录）→ 404 CONTENT_010
        rb = client.post(
            "/api/v1/auth/wechat",
            json={"code": f"dt-b-{uuid.uuid4().hex[:8]}", "device_id": "dt-b-dev"},
        )
        assert rb.status_code == 200
        headers_b = {"Authorization": f"Bearer {rb.json()['data']['access_token']}"}
        r2 = client.get(f"/api/v1/contents/{cid}", headers=headers_b)
        assert r2.status_code == 404, f"他人应 404: {r2.status_code}"
        assert r2.json()["code"] == "CONTENT_010", f"错误码: {r2.json()['code']}"
        # ③ 软删 → 404（回收站走 /trash）
        rd = client.delete(f"/api/v1/contents/{cid}", headers=auth_headers)
        assert rd.status_code == 200, rd.text
        r3 = client.get(f"/api/v1/contents/{cid}", headers=auth_headers)
        assert r3.status_code == 404, f"软删应 404: {r3.status_code}"
        assert r3.json()["code"] == "CONTENT_010"
        # ④ 畸形 ID → 同语义 404（R4#2 口径）
        r4 = client.get("/api/v1/contents/not-a-uuid", headers=auth_headers)
        assert r4.status_code == 404, f"畸形 ID 应 404: {r4.status_code}"
        assert r4.json()["code"] == "CONTENT_010"
    finally:
        from app.db.models import Content
        from app.db.session import SessionLocal
        from sqlalchemy import delete as sa_delete

        db = SessionLocal()
        try:
            db.execute(sa_delete(Content).where(Content.user_id == user_id))
            db.commit()
        finally:
            db.close()
