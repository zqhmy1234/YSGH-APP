"""向量库 collection 隔离测试（审查 MAJOR：基准评测不污染生产检索空间）

用 stub 客户端验证 upsert/search/ensure_collection 的 collection 路由：
- 默认 → 生产 yishu_contents
- 基准评测显式传 collection → 写入/检索独立库（yishu_benchmark）
"""


import pytest
from app.services.vector_store import COLLECTION, VectorStore
from qdrant_client.http import models


class _StubClient:
    """最小 Qdrant 客户端桩：记录 collection 调用，返回空结果"""

    def __init__(self):
        self.collections: set[str] = set()
        self.upsert_calls: list[str] = []
        self.query_calls: list[str] = []

    def get_collections(self):
        class _Names:
            def __init__(self, cols):
                self.collections = [type("C", (), {"name": c})() for c in cols]
        return _Names(self.collections)

    def create_collection(self, collection_name, **kwargs):
        self.collections.add(collection_name)

    def upsert(self, collection_name, points):
        self.upsert_calls.append(collection_name)

    def query_points(self, collection_name, **kwargs):
        self.query_calls.append(collection_name)
        return type("R", (), {"points": []})()

    def retrieve(self, collection_name, ids, with_vectors=False):
        # 桩：无存量点（新写入路径），返回空列表
        return []


@pytest.fixture()
def store():
    s = object.__new__(VectorStore)  # 绕过 __init__（避免连接真实 Qdrant）
    s.client = _StubClient()
    return s


def test_default_uses_production_collection(store):
    """默认路径 → 生产 collection yishu_contents"""
    store.upsert_content("c-1", "你好", [0.0] * 4, {1: 0.5}, {})
    assert store.client.upsert_calls == [COLLECTION]
    store.search([0.0] * 4, {1: 0.5})
    assert store.client.query_calls == [COLLECTION, COLLECTION]  # dense + sparse


def test_benchmark_collection_isolated(store):
    """基准评测显式 collection → 独立库，不碰生产"""
    bench = "yishu_benchmark"
    store.ensure_collection(bench)
    assert bench in store.client.collections
    assert COLLECTION not in store.client.collections  # 生产 collection 不被顺带创建

    store.upsert_content("rag-b-1", "基准语料", [0.0] * 4, {1: 0.5}, {}, collection=bench)
    assert store.client.upsert_calls == [bench]
    store.search([0.0] * 4, {1: 0.5}, collection=bench)
    assert store.client.query_calls == [bench, bench]

    # 生产路径不受影响
    assert store.client.upsert_calls.count(COLLECTION) == 0
    assert store.client.query_calls.count(COLLECTION) == 0


def test_ensure_collection_idempotent(store):
    """ensure_collection 幂等：已存在不重复建"""
    store.ensure_collection(COLLECTION)
    first = set(store.client.collections)
    store.ensure_collection(COLLECTION)
    assert set(store.client.collections) == first


def test_upsert_merged_keeps_existing_vectors(monkeypatch):
    """_upsert_merged：已有向量/存量 payload 不被整点替换冲掉（B2-4 回归）

    Qdrant upsert 是 replace 语义——upsert_content 后再 upsert_image_vec
    必须保留 text_vec/text_sparse（反之亦然）。
    """
    s = object.__new__(VectorStore)
    stub = _StubClient()
    stub.collections.add(COLLECTION)

    class _Vec:
        def __init__(self, d, payload=None):
            self.vector = d
            self.payload = payload

    existing = {"text_vec": [0.1] * 4, "text_sparse": models.SparseVector(indices=[1], values=[0.5])}
    retrieved = [_Vec(existing, {"content_id": "c-1", "old_field": "keep"})]
    stub.retrieve = lambda collection_name, ids, with_vectors=False: retrieved

    upserted = {}

    def fake_upsert(collection_name, points):
        p = points[0]
        upserted["vector"] = p.vector
        upserted["payload"] = p.payload

    stub.upsert = fake_upsert
    s.client = stub

    # 第二次写入只带 image_vec → 应合并保留 text_vec/text_sparse + 存量 payload
    s._upsert_merged(COLLECTION, "pid-1", {"image_vec": [0.2] * 4}, {"content_id": "c-1", "content_type": "image"})

    assert set(upserted["vector"].keys()) == {"text_vec", "text_sparse", "image_vec"}
    assert upserted["payload"]["content_id"] == "c-1"
    assert upserted["payload"]["content_type"] == "image"  # 新 payload 覆盖存量字段
    # 存量 payload 其他字段保留
    assert upserted["payload"]["old_field"] == "keep"
