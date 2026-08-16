"""内容 API 测试（对齐测试清单 API-002/005：上传主链路 + 参数边界）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    return TestClient(app)


def test_create_text_content(client):
    """文字碎片入库（F2）"""
    r = client.post(
        "/api/v1/contents",
        json={"content_type": "text", "text": "明天记得买咖啡豆", "source": "app"},
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["content_type"] == "text"
    assert data["status"] == "processing"  # 异步 AI 管线前状态


def test_create_photo_content(client):
    """照片入库（F1）"""
    r = client.post(
        "/api/v1/contents",
        json={"content_type": "photo", "cos_key": "photos/x.jpg", "source": "app"},
    )
    assert r.status_code == 200
    assert r.json()["data"]["content_type"] == "photo"


def test_create_invalid_type(client):
    """非法 content_type → 422（API-005 参数边界）"""
    r = client.post(
        "/api/v1/contents",
        json={"content_type": "video", "source": "app"},
    )
    assert r.status_code == 422


def test_presign_upload(client):
    """COS STS 预签名（决策 #10/SYNC-013）"""
    r = client.post("/api/v1/contents/presign", json={})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["cos_presign"]["tmp_secret_id"]
    assert data["cos_presign"]["session_token"]


def test_list_contents(client):
    """内容列表分页（API-006）"""
    r = client.get("/api/v1/contents?limit=5")
    assert r.status_code == 200
    assert isinstance(r.json()["data"], list)
