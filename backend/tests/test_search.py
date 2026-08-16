"""检索 API 测试（对齐测试清单 API-003/RET-018：搜索主链路 + 溯源）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    return TestClient(app)


def test_search_basic(client):
    """描述性搜索主链路（API-003）"""
    r = client.post("/api/v1/search", json={"q": "杭州旅行"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["hits"]
    assert data["total"] > 0
    assert data["intent"] in ("text", "image", "mixed")


def test_search_trace_present(client):
    """溯源字段（RET-016：每条结果可解释命中）"""
    r = client.post("/api/v1/search", json={"q": "杭州"})
    data = r.json()["data"]
    for hit in data["hits"]:
        assert hit["trace"], "搜索命中必须带溯源解释"


def test_search_latency_metric(client):
    """延迟指标字段（RET-018 验收前置）"""
    r = client.post("/api/v1/search", json={"q": "吃饭"})
    assert r.json()["data"]["latency_ms"] >= 0


def test_search_limit(client):
    """limit 截断（RET-006）"""
    r = client.post("/api/v1/search", json={"q": "x", "limit": 1})
    assert len(r.json()["data"]["hits"]) <= 1


def test_search_empty_query(client):
    """空查询 → 422"""
    r = client.post("/api/v1/search", json={"q": ""})
    assert r.status_code == 422


def test_search_filters(client):
    """payload filter 参数（时间/类型，B2 过滤层）"""
    r = client.post(
        "/api/v1/search",
        json={"q": "旅行", "content_types": ["photo"], "limit": 5},
    )
    assert r.status_code == 200
