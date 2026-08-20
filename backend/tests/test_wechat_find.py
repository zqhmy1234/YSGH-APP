"""微信"找"测试（S4-02 沙箱链路 · WP-G）

覆盖：
  - 需登录（401）
  - 正常找：reply 组装 + hits + latency_ms（沙箱 10s/3s 门禁前置）
  - 空查询 → 422
  - limit 截断
前置：Qdrant + BGE-M3 本地可用（同 test_search.py）
"""
import uuid

import pytest
from app.main import app
from fastapi.testclient import TestClient

pytestmark = pytest.mark.rag


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def auth_headers(client):
    r = client.post(
        "/api/v1/auth/wechat",
        json={"code": f"wf-{uuid.uuid4().hex[:8]}", "device_id": "wf-dev"},
    )
    assert r.status_code == 200
    token = r.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_find_requires_auth(client):
    r = client.post("/api/v1/wechat/find", json={"query": "杭州"})
    assert r.status_code == 401


def test_find_returns_reply(client, auth_headers):
    r = client.post("/api/v1/wechat/find", json={"query": "杭州旅行"}, headers=auth_headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert "reply" in data and data["reply"]
    assert data["hits"] >= 0
    assert data["latency_ms"] >= 0
    assert data["latency_ms"] < 10_000, "沙箱 10s 门禁（WX-007 前置）"


def test_find_empty_query_422(client, auth_headers):
    r = client.post("/api/v1/wechat/find", json={"query": ""}, headers=auth_headers)
    assert r.status_code == 422


def test_find_limit_truncation(client, auth_headers):
    r = client.post("/api/v1/wechat/find", json={"query": "吃饭", "limit": 1}, headers=auth_headers)
    data = r.json()["data"]
    assert data["hits"] <= 1
