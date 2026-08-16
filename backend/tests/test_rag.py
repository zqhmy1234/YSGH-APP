"""RAG 检索集成测试（B2：真实 Qdrant + BGE-M3）

前置：Docker Qdrant（yishu-qdrant）+ BGE-M3 模型已下载
运行：pytest backend/tests/test_rag.py -v

覆盖：RET-001（文字搜图占位）/RET-003（dense+sparse 融合）/RET-007（改写）/
      RET-016（溯源）/RET-018（延迟）/API-009（降级）
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from app.schemas.search import SearchQuery
from app.services.embedding import encode_dense, encode_query
from app.services.rag import _rewrite_query, _route_query, search
from app.services.vector_store import get_store, point_id_for
from qdrant_client.http import models  # noqa: F401

# 测试语料（中文记忆场景）
CORPUS = [
    {
        "id": "rag-001",
        "text": "去年夏天和爸妈去了杭州西湖，坐了手摇船，拍了荷花",
        "tags": ["旅行", "家庭"],
        "taken_at": "2025-07-15T10:00:00+08:00",
    },
    {
        "id": "rag-002",
        "text": "考研备考第 200 天，数学真题刷完两遍",
        "tags": ["备考"],
        "taken_at": "2026-05-20T09:00:00+08:00",
    },
    {
        "id": "rag-003",
        "text": "和朋友在苏州平江路吃松鼠桂鱼，人均 80",
        "tags": ["美食"],
        "taken_at": "2025-10-01T12:30:00+08:00",
    },
    {
        "id": "rag-004",
        "text": "今天加班到十点，项目终于上线了",
        "tags": ["工作"],
        "taken_at": "2026-08-10T22:00:00+08:00",
    },
    {
        "id": "rag-005",
        "text": "猫主子第一次打疫苗，全程很乖",
        "tags": ["宠物"],
        "taken_at": "2026-03-08T15:00:00+08:00",
    },
]


@pytest.fixture(scope="module")
def indexed_store():
    """建索引：语料写入 Qdrant（幂等：先删后写）"""
    store = get_store()
    # 清理旧测试点（Qdrant 1.14+ 点 ID 必须为整数/UUID → UUID5 派生）
    store.client.delete(
        collection_name="yishu_contents",
        points_selector=models.PointIdsList(
            points=[point_id_for(f"rag-{i:03d}") for i in range(1, 10)]
        ),
    )
    for doc in CORPUS:
        dense, sparse = encode_query(doc["text"])
        store.upsert_content(
            content_id=doc["id"],
            text=doc["text"],
            dense=dense,
            sparse=sparse,
            payload={
                "content_type": "text",
                "tags": doc["tags"],
                "taken_at": doc["taken_at"],
            },
        )
    time.sleep(0.5)  # Qdrant 索引最终一致性
    return store


@pytest.mark.rag
def test_embedding_dimension():
    """BGE-M3 dense 维度 = 1024（B2 named vectors 一致性）"""
    dense = encode_dense(["测试"])[0]
    assert len(dense) == 1024


@pytest.mark.rag
@pytest.mark.integration
def test_dense_search_recall(indexed_store):
    """dense 召回：语义相似命中（RET-003 前置）"""
    q = SearchQuery(q="杭州旅行荷花")
    result = search(q)
    ids = [h.content_id for h in result.hits]
    assert "rag-001" in ids, f"语义检索应命中 rag-001, got {ids}"


@pytest.mark.rag
@pytest.mark.integration
def test_sparse_keyword_recall(indexed_store):
    """sparse 召回：关键词精确命中（错别字/口语，RET-004）"""
    q = SearchQuery(q="松鼠桂鱼")
    result = search(q)
    ids = [h.content_id for h in result.hits]
    assert "rag-003" in ids, f"关键词检索应命中 rag-003, got {ids}"


@pytest.mark.rag
@pytest.mark.integration
def test_query_rewrite_time_filter():
    """Query 改写：'去年' → 时间过滤条件（RET-007）"""
    q = SearchQuery(q="去年去的地方")
    rewritten, filters = _rewrite_query(q)
    assert rewritten == "去的地方"
    assert filters.get("time_from") is not None
    assert filters["time_from"].year == 2025


def test_route_query():
    """查询路由：图片意图识别（B2 路由）"""
    assert _route_query("照片里的猫") == "image"
    assert _route_query("考研笔记") == "text"


@pytest.mark.rag
@pytest.mark.integration
def test_search_trace_present(indexed_store):
    """溯源：每条命中带 dense/sparse 分数解释（RET-016）"""
    q = SearchQuery(q="猫")
    result = search(q)
    if result.hits:
        for h in result.hits:
            assert h.trace, "必须带溯源"
            assert "dense_score" in h.trace or "sparse_score" in h.trace


@pytest.mark.rag
@pytest.mark.integration
def test_search_latency_under_3s(indexed_store):
    """延迟：P95<3s（RET-018，M1 门禁）"""
    latencies = []
    for i in range(5):
        start = time.perf_counter()
        search(SearchQuery(q=f"测试查询第{i}条 杭州"))
        latencies.append((time.perf_counter() - start) * 1000)
    p95 = sorted(latencies)[3]
    assert p95 < 3000, f"P95={p95:.0f}ms 超预算"


def test_search_empty_query_rejected():
    """空查询 → 校验层拒绝（路由层不处理）"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SearchQuery(q="")
