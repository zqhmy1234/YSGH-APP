"""检索 API 测试（对齐测试清单 API-003/RET-018：搜索主链路 + 溯源）

安全修复后：搜索需登录（个人记忆检索非公共接口）。
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
        json={"code": f"sr-{uuid.uuid4().hex[:8]}", "device_id": "sr-dev"},
    )
    assert r.status_code == 200
    token = r.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_search_requires_auth(client):
    """安全修复：未登录 → 401"""
    r = client.post("/api/v1/search", json={"q": "杭州旅行"})
    assert r.status_code == 401


def test_search_basic(client, auth_headers):
    """描述性搜索主链路（API-003）——用户隔离：先给本用户造一条内容再搜索

    2026-08-26：检索阶段按 user_id 过滤（召回隔离修复），
    新用户无数据时 hits 为空是正确行为；测试需先造数。
    """
    from app.services.pipeline import process_content

    text = f"杭州旅行记录-{uuid.uuid4().hex[:6]}-西湖边散步"
    r = client.post(
        "/api/v1/contents",
        json={"content_type": "text", "text": text, "source": "app"},
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    cid = r.json()["data"]["id"]
    res = process_content(cid)
    assert res["status"] == "done", res

    r = client.post(
        "/api/v1/search",
        json={"q": "杭州旅行", "limit": 20},
        headers=auth_headers,
    )
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["hits"], f"搜索未命中本用户内容: {data}"
    assert data["total"] > 0
    assert data["intent"] in ("text", "image", "mixed")


def test_search_trace_present(client, auth_headers):
    """溯源字段（RET-016：每条结果可解释命中）"""
    r = client.post("/api/v1/search", json={"q": "杭州"}, headers=auth_headers)
    data = r.json()["data"]
    for hit in data["hits"]:
        assert hit["trace"], "搜索命中必须带溯源解释"


def test_search_latency_metric(client, auth_headers):
    """延迟指标字段（RET-018 验收前置）"""
    r = client.post("/api/v1/search", json={"q": "吃饭"}, headers=auth_headers)
    assert r.json()["data"]["latency_ms"] >= 0


def test_search_limit(client, auth_headers):
    """limit 截断（RET-006）"""
    r = client.post("/api/v1/search", json={"q": "x", "limit": 1}, headers=auth_headers)
    assert len(r.json()["data"]["hits"]) <= 1


def test_search_empty_query(client, auth_headers):
    """空查询 → 422（需登录后校验 body；认证先于 body 校验）"""
    r = client.post("/api/v1/search", json={"q": ""}, headers=auth_headers)
    assert r.status_code == 422


def test_search_filters(client, auth_headers):
    """payload filter 参数（时间/类型，B2 过滤层）"""
    r = client.post(
        "/api/v1/search",
        json={"q": "旅行", "content_types": ["photo"], "limit": 5},
        headers=auth_headers,
    )
    assert r.status_code == 200
