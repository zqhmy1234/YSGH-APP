"""照片 multipart 中转上传测试（客户端第一波 B-BE-1/2/3 · 2026-08-24）

覆盖：成功链路（落库+storage+管线入队）/ 去重 409 / 护栏 422 / 未授权 401 /
      文件校验（类型白名单/空文件/超限 413）/ meta 边界（坏 JSON/越界 GPS/非法 source）
"""
import json
import uuid

import pytest
from app.api import contents as contents_api
from app.main import app
from app.services.external.storage import FakeStorageBackend, get_storage_backend
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def auth_headers(client):
    r = client.post(
        "/api/v1/auth/wechat",
        json={"code": f"cu-{uuid.uuid4().hex[:8]}", "device_id": "cu-dev"},
    )
    assert r.status_code == 200
    token = r.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _upload(client, headers, *, filename="photo.jpg", content=b"fake-jpeg-bytes", meta=None):
    files = {"file": (filename, content, "image/jpeg")}
    data = {"meta": json.dumps(meta or {}, ensure_ascii=False)}
    return client.post("/api/v1/contents/upload", files=files, data=data, headers=headers)


def test_upload_photo_success(client, auth_headers):
    """成功链路：multipart 上传 → 落库（photo/processing）+ storage 原件 + 管线入队"""
    meta = {
        "taken_at": "2026-08-24T10:00:00+08:00",
        "gps_lat": 31.2304,
        "gps_lng": 121.4737,
        "perceptual_hash": f"ph-{uuid.uuid4().hex[:10]}",
        "source": "app",
        "extra": {"width": 3024, "height": 4032},
    }
    r = _upload(client, auth_headers, meta=meta)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["content_type"] == "photo"
    assert data["status"] == "processing"

    # storage 原件存在（fake 后端：cos_key 形如 photos/<user>/<hex>.jpg）
    backend = get_storage_backend()
    assert isinstance(backend, FakeStorageBackend)
    keys = [k for k in backend._store if k.startswith("photos/")]
    assert keys, "storage 未收到原件"
    assert backend._store[keys[0]] == b"fake-jpeg-bytes"


def test_upload_duplicate_hash_rejected(client, auth_headers):
    """同用户同感知哈希 → 409 CONTENT_002（Q16 去重，与 create_content 语义一致）"""
    meta = {"perceptual_hash": "ph-dup-upload", "source": "app"}
    r1 = _upload(client, auth_headers, meta=meta)
    assert r1.status_code == 200
    r2 = _upload(client, auth_headers, meta=meta)
    assert r2.status_code == 409
    assert r2.json()["code"] == "CONTENT_002"


def test_upload_requires_auth(client):
    """未带 token → 401（AUTH-005）"""
    r = _upload(client, {})
    assert r.status_code == 401


def test_upload_invalid_file_type(client, auth_headers):
    """非图片文件（.txt + text/plain）→ 422 CONTENT_006"""
    r = client.post(
        "/api/v1/contents/upload",
        files={"file": ("note.txt", b"hello", "text/plain")},
        data={"meta": "{}"},
        headers=auth_headers,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "CONTENT_006"


def test_upload_empty_file(client, auth_headers):
    """空文件 → 422 CONTENT_006"""
    r = _upload(client, auth_headers, content=b"")
    assert r.status_code == 422
    assert r.json()["code"] == "CONTENT_006"


def test_upload_oversize_rejected(client, auth_headers, monkeypatch):
    """超上限 → 413 CONTENT_007（monkeypatch 缩小上限避免真发 20MB）"""
    monkeypatch.setattr(contents_api, "MAX_PHOTO_BYTES", 100)
    r = _upload(client, auth_headers, content=b"x" * 200)
    assert r.status_code == 413
    assert r.json()["code"] == "CONTENT_007"


def test_upload_bad_meta(client, auth_headers):
    """meta 非 JSON / 非法 GPS / 非法 source → 422 CONTENT_005"""
    r1 = client.post(
        "/api/v1/contents/upload",
        files={"file": ("a.jpg", b"x", "image/jpeg")},
        data={"meta": "not-json"},
        headers=auth_headers,
    )
    assert r1.status_code == 422
    assert r1.json()["code"] == "CONTENT_005"

    r2 = _upload(client, auth_headers, meta={"gps_lat": 999, "source": "app"})
    assert r2.status_code == 422
    assert r2.json()["code"] == "CONTENT_005"

    r3 = _upload(client, auth_headers, meta={"source": "hacker"})
    assert r3.status_code == 422
    assert r3.json()["code"] == "CONTENT_005"


def test_upload_guardrail_reject(client, auth_headers):
    """meta.text 命中敏感词 → 422 CONTENT_003（B5b 护栏复用 moderate）"""
    meta = {"text": "支持法轮功的言论", "source": "app"}
    r = _upload(client, auth_headers, meta=meta)
    assert r.status_code == 422
    assert r.json()["code"] == "CONTENT_003"


def test_upload_heic_ext_ok(client, auth_headers):
    """HEIC 扩展名白名单放行（客户端常见格式）"""
    r = _upload(client, auth_headers, filename="photo.heic", content=b"heic-bytes")
    assert r.status_code == 200
