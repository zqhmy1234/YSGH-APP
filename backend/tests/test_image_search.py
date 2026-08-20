"""以图搜图（B2-4）+ mixed 双路融合测试（2026-08-19）

覆盖：
  - image_vec upsert + search_image 检索（真实 Qdrant + BGE-M3）
  - rag.search_by_image：caption 走 mock/注入，验证检索链路与降级
  - mixed 路由双路召回合并（_merge_recalls）
前置：Docker Qdrant（yishu-qdrant）+ BGE-M3 已下载（同 test_rag）。
"""
import time

import pytest
from app.schemas.search import SearchQuery
from app.services.embedding import encode_dense
from app.services.rag import _merge_recalls, search_by_image
from app.services.vector_store import get_store, point_id_for
from qdrant_client.http import models  # noqa: F401

# 图片语料（caption 即图片语义描述）
IMAGE_CORPUS = [
    {"id": "img-001", "caption": "西湖边的荷花盛开，游船在湖面上", "label": "screenshot"},
    {"id": "img-002", "caption": "苏州园林的假山和亭子", "label": "screenshot"},
    {"id": "img-003", "caption": "会议室投影仪上的课程表截图", "label": "screenshot"},
]


@pytest.fixture(scope="module")
def image_indexed():
    """写入 image_vec 测试点（yishu_benchmark 独立 collection，不污染生产）"""
    store = get_store()
    store.ensure_collection("yishu_benchmark")
    store.client.delete(
        collection_name="yishu_benchmark",
        points_selector=models.PointIdsList(
            points=[point_id_for(f"img-{i:03d}") for i in range(1, 10)]
        ),
    )
    for doc in IMAGE_CORPUS:
        vec = encode_dense([doc["caption"]])[0]
        payload = {
            "content_type": "image",
            "label": doc["label"],
            "benchmark": "rag-distribution",
            "text": doc["caption"],
        }
        store.upsert_image_vec(content_id=doc["id"], vec=vec, payload=payload, collection="yishu_benchmark")
    time.sleep(0.5)
    return store


@pytest.mark.rag
@pytest.mark.integration
def test_search_image_recall(image_indexed):
    """以图搜图：语义相似 caption 命中（image_vec 余弦检索）"""
    store = image_indexed
    query_vec = encode_dense(["荷花游船西湖"])[0]
    hits = store.search_image(query_vec, filters={"content_types": ["image"]}, limit=5, collection="yishu_benchmark")
    ids = [h["content_id"] for h in hits]
    assert "img-001" in ids, f"image_vec 应命中 img-001, got {ids}"


@pytest.mark.rag
@pytest.mark.integration
def test_search_by_image_service(image_indexed, monkeypatch):
    """服务层：图片 → caption（注入）→ image_vec 检索 → 同构结果"""
    import app.services.external.dashscope as ds_mod

    monkeypatch.setattr(
        ds_mod,
        "image_caption",
        lambda path: "西湖边的荷花盛开，游船在湖面上",
    )
    result = search_by_image("C:/fake/query.jpg", SearchQuery(q="[image]"), collection="yishu_benchmark")
    assert result.intent == "image"
    assert result.rewritten_query == "西湖边的荷花盛开，游船在湖面上"
    ids = [h.content_id for h in result.hits]
    assert "img-001" in ids, f"以图搜图应命中 img-001, got {ids}"


@pytest.mark.rag
@pytest.mark.integration
def test_search_by_image_degraded(monkeypatch):
    """图片塔不可用 → degraded 标记 + 空结果（不抛异常）"""
    import app.services.external.dashscope as ds_mod

    def boom(path):
        raise RuntimeError("百炼不可用")

    monkeypatch.setattr(ds_mod, "image_caption", boom)
    result = search_by_image("C:/fake/query.jpg", SearchQuery(q="[image]"))
    assert result.degraded is True
    assert result.hits == []


def test_merge_recalls_dedupe_keep_max():
    """多路召回合并：去重 + 保留最高分 + 排序"""
    a = [{"content_id": "x", "score": 0.5}, {"content_id": "y", "score": 0.3}]
    b = [{"content_id": "x", "score": 0.9}, {"content_id": "z", "score": 0.4}]
    merged = _merge_recalls([a, b])
    assert [m["content_id"] for m in merged] == ["x", "z", "y"]
    assert merged[0]["score"] == 0.9
