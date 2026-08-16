"""Qdrant 向量库访问层（B2 设计：named vectors + dense/sparse 混合检索）

- 单 collection + content_type 字段（B2-2 跨栏目）
- named vectors：text_vec（BGE-M3 dense 1024）+ image_vec（Qwen3-VL，M1 后接）
- sparse：BGE-M3 lexical（关键词路）
- 检索：dense+sparse 走 RRF 融合（WeKnora 参数：0.7/0.3, k=60）
"""
from __future__ import annotations

import logging
import uuid
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.http import models

logger = logging.getLogger("yishu.rag")

COLLECTION = "yishu_contents"
DENSE_VEC_NAME = "text_vec"
IMAGE_VEC_NAME = "image_vec"
VECTOR_SIZE = 1024  # BGE-M3 dense 维度


def point_id_for(content_id: str) -> str:
    """内容 ID → Qdrant 点 ID（UUID5 稳定映射）

    Qdrant 1.14+ 只接受无符号整数或 UUID 作点 ID；字符串 ID（如 rag-001）会被拒。
    用 UUID5 从 content_id 稳定派生，保证幂等（同内容 → 同点）。
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, content_id))


class VectorStore:
    """Qdrant 封装（进程内单例）"""

    def __init__(self, url: str | None = None):
        self.client = QdrantClient(url=url or "http://localhost:6333")
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        """建 collection（幂等）：named vectors text_vec + 预留 image_vec"""
        existing = self.client.get_collections().collections
        if any(c.name == COLLECTION for c in existing):
            return
        self.client.create_collection(
            collection_name=COLLECTION,
            vectors_config={
                DENSE_VEC_NAME: models.VectorParams(size=VECTOR_SIZE, distance=models.Distance.COSINE),
                IMAGE_VEC_NAME: models.VectorParams(size=VECTOR_SIZE, distance=models.Distance.COSINE),
            },
            sparse_vectors_config={
                "text_sparse": models.SparseVectorParams(
                    modifier=models.Modifier.IDF
                )
            },
        )
        logger.info("Qdrant collection 已创建: %s", COLLECTION)

    def upsert_content(
        self,
        content_id: str,
        text: str,
        dense: list[float],
        sparse: dict[str, float],
        payload: dict,
    ) -> None:
        """写入内容向量（text_vec dense + text_sparse）"""
        self.client.upsert(
            collection_name=COLLECTION,
            points=[
                models.PointStruct(
                    id=point_id_for(content_id),
                    vector={
                        DENSE_VEC_NAME: dense,
                        "text_sparse": models.SparseVector(
                            indices=list(sparse.keys()),
                            values=list(sparse.values()),
                        ),
                    },
                    payload={"content_id": content_id, **payload},
                )
            ],
        )

    def search(
        self,
        dense: list[float],
        sparse: dict[str, float],
        filters: dict | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """混合检索：dense + sparse 并行召回 → RRF 融合（WeKnora 0.7/0.3, k=60）"""
        # dense 路（query_points + using 指定 named vector；qdrant-client 1.19）
        dense_hits = self.client.query_points(
            collection_name=COLLECTION,
            query=dense,
            using=DENSE_VEC_NAME,
            query_filter=self._to_filter(filters),
            limit=limit,
        ).points

        # sparse 路
        sparse_hits = []
        if sparse:
            sparse_hits = self.client.query_points(
                collection_name=COLLECTION,
                query=models.SparseVector(
                    indices=list(sparse.keys()),
                    values=list(sparse.values()),
                ),
                using="text_sparse",
                query_filter=self._to_filter(filters),
                limit=limit,
            ).points

        return self._rrf_fuse(dense_hits, sparse_hits, limit=limit)

    @staticmethod
    def _rrf_fuse(dense_hits, sparse_hits, limit: int = 50, k: int = 60) -> list[dict]:
        """RRF 融合（Reciprocal Rank Fusion）"""
        scores: dict[str, float] = {}
        details: dict[str, dict] = {}

        for rank, hit in enumerate(dense_hits, 1):
            pid = str(hit.payload.get("content_id", hit.id)) if hit.payload else str(hit.id)
            scores[pid] = scores.get(pid, 0.0) + 0.7 / (k + rank)
            details.setdefault(pid, {"dense_score": hit.score, "sparse_score": 0.0})
            details[pid]["dense_score"] = hit.score

        for rank, hit in enumerate(sparse_hits, 1):
            pid = str(hit.payload.get("content_id", hit.id)) if hit.payload else str(hit.id)
            scores[pid] = scores.get(pid, 0.0) + 0.3 / (k + rank)
            d = details.setdefault(pid, {"dense_score": 0.0, "sparse_score": 0.0})
            d["sparse_score"] = hit.score

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
        result = []
        for pid, score in ranked:
            d = details[pid]
            result.append({
                "content_id": pid,
                "score": round(score, 4),
                "dense_score": round(d["dense_score"], 4),
                "sparse_score": round(d["sparse_score"], 4),
            })
        return result

    @staticmethod
    def _to_filter(filters: dict | None) -> models.Filter | None:
        """payload filter（时间/类型/地点/实体 tag，B2-2：过滤层不是召回路）"""
        if not filters:
            return None
        must: list = []
        for key, value in filters.items():
            if key == "content_types" and value:
                must.append(models.FieldCondition(
                    key="content_type",
                    match=models.MatchAny(any=value),
                ))
            elif key == "time_from" and value:
                must.append(models.FieldCondition(
                    key="taken_at",
                    range=models.Range(gte=value.isoformat()),
                ))
            elif key == "time_to" and value:
                must.append(models.FieldCondition(
                    key="taken_at",
                    range=models.Range(lte=value.isoformat()),
                ))
            elif key == "place" and value:
                must.append(models.FieldCondition(
                    key="place",
                    match=models.MatchValue(value=value),
                ))
            elif key == "tag" and value:
                must.append(models.FieldCondition(
                    key="tags",
                    match=models.MatchValue(value=value),
                ))
        return models.Filter(must=must) if must else None


@lru_cache(maxsize=1)
def get_store() -> VectorStore:
    """获取单例（lru_cache 缓存实例，延迟初始化）"""
    return VectorStore()
