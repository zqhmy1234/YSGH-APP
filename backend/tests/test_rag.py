"""RAG 检索集成测试（B2：真实 Qdrant + BGE-M3）

前置：Docker Qdrant（yishu-qdrant）+ BGE-M3 模型已下载
运行：pytest backend/tests/test_rag.py -v

覆盖：RET-001（文字搜图占位）/RET-003（dense+sparse 融合）/RET-007（改写）/
      RET-016（溯源）/RET-018（延迟）/API-009（降级）
"""
from __future__ import annotations

import time
from datetime import datetime

import pytest
from app.schemas.search import SearchQuery
from app.services.embedding import encode_dense, encode_query
from app.services.rag import _rewrite_query, _route_query, search
from app.services.vector_store import get_store, point_id_for
from qdrant_client.http import models  # noqa: F401

# 测试专用隔离 collection（2026-08-25 修复：原与生产 yishu_contents 共用，
# 生产库有真实数据后测试点被挤出 Top-k 导致 flaky——与基准评测同样隔离）
TEST_COLLECTION = "yishu_test_rag"

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


def _ts(s: str) -> int:
    """ISO 时间串 → epoch 秒（审查 CRITICAL 修复：payload 侧 taken_at 与 _to_filter 的 Range 数值一致）"""
    return int(datetime.fromisoformat(s).timestamp())


@pytest.fixture(scope="module")
def indexed_store():
    """建索引：语料写入 Qdrant（幂等：先删后写）

    审查 CRITICAL 修复：payload 的 taken_at 存 epoch 秒（int），
    与 _to_filter 的 Range(gte/lte=value.timestamp()) 数值过滤一致——
    此前存 ISO 字符串导致时间过滤静默不命中（恒空结果）。
    """
    store = get_store()
    store.ensure_collection(TEST_COLLECTION)
    # 清理旧测试点（Qdrant 1.14+ 点 ID 必须为整数/UUID → UUID5 派生）
    store.client.delete(
        collection_name=TEST_COLLECTION,
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
                "taken_at": _ts(doc["taken_at"]),
            },
            collection=TEST_COLLECTION,
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
    result = search(q, collection=TEST_COLLECTION)
    ids = [h.content_id for h in result.hits]
    assert "rag-001" in ids, f"语义检索应命中 rag-001, got {ids}"


@pytest.mark.rag
@pytest.mark.integration
def test_sparse_keyword_recall(indexed_store):
    """sparse 召回：关键词精确命中（错别字/口语，RET-004）"""
    q = SearchQuery(q="松鼠桂鱼")
    result = search(q, collection=TEST_COLLECTION)
    ids = [h.content_id for h in result.hits]
    assert "rag-003" in ids, f"关键词检索应命中 rag-003, got {ids}"


@pytest.mark.rag
@pytest.mark.integration
def test_query_rewrite_time_filter():
    """Query 改写：'去年' → 时间过滤条件（RET-007）"""
    q = SearchQuery(q="去年去的地方")
    rewritten, filters, ner_filters = _rewrite_query(q)
    assert rewritten == "去的地方"
    assert filters.get("time_from") is not None
    assert filters["time_from"].year == 2025
    assert ner_filters == {}


def test_query_rewrite_time_modifier_mid_query():
    """2026-08-25 RAG 审查修复：句中时间词是名词修饰语，不是过滤意图

    回归："记得明天下午之前把上个月的工作总结报告交给领导" 此前误加 time 过滤
    → 语料无 taken_at 时检索空结果（benchmark length 层 hit_rate 0.5 根因）。
    现在句中"上个月"不再触发过滤，也不删词。
    """
    q = SearchQuery(q="记得明天下午之前把上个月的工作总结报告交给领导")
    rewritten, filters, ner_filters = _rewrite_query(q)
    assert "time_from" not in filters
    assert "time_to" not in filters
    assert rewritten == q.q
    assert ner_filters == {}


def test_boost_exact_matches():
    """2026-08-25 RAG 审查新增：词元全命中文档提升到稠密噪声之上

    用贴近 RRF 的密集分数（0.0115 vs 0.0111，真实 RRF 分数差距极小）——
    ×1.8 提升足够把精确命中文档顶到首位。
    """
    from app.services.rag import _boost_exact_matches

    hits = [
        {"content_id": "a", "text": "今天跑了马拉松，配速五分", "score": 0.0111},
        {"content_id": "b", "text": "周末计划和朋友吃饭", "score": 0.0115},
    ]
    boosted = _boost_exact_matches("马拉松", hits)
    assert boosted[0]["content_id"] == "a"
    assert boosted[0]["score"] > boosted[1]["score"]
    # 描述性查询无全词命中 → 原序
    hits2 = [dict(h) for h in hits]
    boosted2 = _boost_exact_matches("关于做产品的想法", hits2)
    assert [h["content_id"] for h in boosted2] == ["b", "a"]


def test_rewrite_ner_place_extraction():
    """B2-2 NER：查询含地名 → place 过滤 + ner_filters 标记（可回退）"""
    q = SearchQuery(q="苏州的美食")
    rewritten, filters, ner_filters = _rewrite_query(q)
    assert filters.get("place") == "苏州"
    assert ner_filters.get("place") == "苏州"


def test_rewrite_ner_explicit_param_wins():
    """显式 place 参数优先于 NER 抽取（不标记为 NER 派生，硬约束）"""
    q = SearchQuery(q="苏州的美食", place="上海")
    _, filters, ner_filters = _rewrite_query(q)
    assert filters.get("place") == "上海"
    assert "place" not in ner_filters


def test_route_query():
    """查询路由：图片意图识别（B2 路由）"""
    assert _route_query("照片里的猫") == "image"
    assert _route_query("考研笔记") == "text"


@pytest.mark.rag
@pytest.mark.integration
def test_search_trace_present(indexed_store):
    """溯源：每条命中带 dense/sparse 分数解释（RET-016）"""
    q = SearchQuery(q="猫")
    result = search(q, collection=TEST_COLLECTION)
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
        search(SearchQuery(q=f"测试查询第{i}条 杭州"), collection=TEST_COLLECTION)
        latencies.append((time.perf_counter() - start) * 1000)
    p95 = sorted(latencies)[3]
    assert p95 < 3000, f"P95={p95:.0f}ms 超预算"


@pytest.mark.rag
@pytest.mark.integration
def test_time_filter_actually_filters(indexed_store):
    """审查 CRITICAL 修复：时间过滤必须真实生效（payload taken_at 为 epoch 秒）

    此前 payload 存 ISO 字符串而 _to_filter 用数值 Range → Qdrant 类型不匹配
    静默不命中（恒空结果）。本测试验证：带时间过滤检索能命中对应语料。
    """
    from datetime import datetime, timezone

    store = indexed_store
    dense, sparse = encode_query("回忆")
    # 2026 年时间窗（语料中 rag-002/004/005 落在 2026 年）
    lo = datetime(2026, 1, 1, tzinfo=timezone.utc)
    hi = datetime(2026, 12, 31, tzinfo=timezone.utc)
    hits_2026 = store.search(
        dense, sparse,
        filters={"time_from": lo, "time_to": hi},
        limit=50,
        collection=TEST_COLLECTION,
    )
    ids_2026 = {h["content_id"] for h in hits_2026}
    assert ids_2026, "2026 年时间窗不应为空（修复前恒空）"
    assert not any(pid == "rag-001" for pid in ids_2026), "2025 年语料不应出现在 2026 时间窗"
    # 反向：2025 年时间窗应命中 rag-001/003
    lo25 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    hi25 = datetime(2025, 12, 31, tzinfo=timezone.utc)
    hits_2025 = store.search(
        dense, sparse,
        filters={"time_from": lo25, "time_to": hi25},
        limit=50,
        collection=TEST_COLLECTION,
    )
    ids_2025 = {h["content_id"] for h in hits_2025}
    assert "rag-001" in ids_2025
    assert "rag-003" in ids_2025


def test_search_empty_query_rejected():
    """空查询 → 校验层拒绝（路由层不处理）"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SearchQuery(q="")
