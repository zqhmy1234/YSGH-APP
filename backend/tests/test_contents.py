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
    r = client.post("/api/v1/contents/presign", json={}, headers=auth_headers)
    assert r.status_code == 404


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
