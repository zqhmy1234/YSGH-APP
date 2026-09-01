"""Qdrant 向量库访问层（B2 设计：named vectors + dense/sparse 混合检索）

- 单 collection + content_type 字段（B2-2 跨栏目）
- named vectors：text_vec（BGE-M3 dense 1024）+ image_vec（Qwen3-VL，M1 后接）
- sparse：BGE-M3 lexical（关键词路）
- 检索：dense+sparse 走 RRF 融合（WeKnora 参数：0.7/0.3, k=60）
- 测试隔离（TD-P1C 2026-08-26）：默认 collection 经 default_collection() 解析——
  测试环境设 QDRANT_COLLECTION=test_* 即写隔离库，cleanup_test_collections()
  每跑结束清理 test_ 前缀 collection（尽力而为）。
"""
from __future__ import annotations

import logging
import os
import uuid
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.http import models

from app.core.config import settings

logger = logging.getLogger("yishu.rag")

COLLECTION = "yishu_contents"
# 测试专用 collection 前缀（TD-P1C 2026-08-26）：测试环境默认写 test_* 隔离库，
# 不污染生产 yishu_contents；每跑结束由 cleanup_test_collections 尽力清理。
TEST_COLLECTION_PREFIX = "test_"
DENSE_VEC_NAME = "text_vec"
IMAGE_VEC_NAME = "image_vec"
VECTOR_SIZE = 1024  # BGE-M3 dense 维度

# FIX-1 content_type 归一（2026-08-26）：生产 photo 点 payload 统一为 "photo"
# （pipeline.py:184/upload.py:211/contents.py:160 均写 "photo"）；历史/基准数据
# 曾用 "image"（build_image_index.py 旧版）。规范值 = "photo"，"image" 视为
# 旧别名——过滤端展开为 MatchAny(["photo", "image"])，两端兼容不丢点。
CONTENT_TYPE_PHOTO = "photo"
CONTENT_TYPE_IMAGE_LEGACY = "image"
CONTENT_TYPE_ALIASES: dict[str, str] = {
    CONTENT_TYPE_IMAGE_LEGACY: CONTENT_TYPE_PHOTO,
}


def default_collection() -> str:
    """生效的默认 collection（TD-P1C 2026-08-26：测试 collection 隔离）

    生产/未设置环境变量 → 生产 yishu_contents；测试环境（CI / api_smoke /
    conftest fixture）通过 `QDRANT_COLLECTION` 环境变量指到 test_* 隔离库，
    使 pipeline 索引与搜索默认不再写生产空间。

    每次调用实时读环境变量（而非模块常量），保证并发进程/测试间切换生效；
    VectorStore 单例仍复用（collection 名只是路由参数，客户端连接不重建）。
    """
    return os.environ.get("QDRANT_COLLECTION") or COLLECTION


def test_collection_name(base: str | None = None) -> str:
    """按前缀生成测试 collection 名（默认基名 = 生产 collection）"""
    return f"{TEST_COLLECTION_PREFIX}{base or COLLECTION}"


def is_test_collection(name: str) -> bool:
    """是否为测试专用 collection（test_ 前缀）"""
    return bool(name) and name.startswith(TEST_COLLECTION_PREFIX)


def cleanup_test_collections(client: QdrantClient | None = None) -> list[str]:
    """清理全部 test_ 前缀的测试 collection（尽力而为，失败不阻断）

    供测试门禁/api_smoke 每跑结束调用；Qdrant 不可达或删除失败仅告警，
    不抛异常——避免清理失败阻断提交（TD-P1C 验收）。
    """
    try:
        c = client or QdrantClient(url=settings.qdrant_url)
        names = [col.name for col in c.get_collections().collections]
        removed: list[str] = []
        for name in names:
            if not is_test_collection(name):
                continue
            try:
                c.delete_collection(collection_name=name)
                removed.append(name)
                logger.info("已清理测试 collection: %s", name)
            except Exception as exc:  # noqa: BLE001 —— 单库删除失败不阻断其余
                logger.warning("清理测试 collection %s 失败: %s", name, exc)
        return removed
    except Exception as exc:  # noqa: BLE001 —— Qdrant 不可达不阻断门禁
        logger.warning("测试 collection 清理跳过（Qdrant 不可达?）: %s", exc)
        return []


def point_id_for(content_id: str) -> str:
    """内容 ID → Qdrant 点 ID（UUID5 稳定映射）

    Qdrant 1.14+ 只接受无符号整数或 UUID 作点 ID；字符串 ID（如 rag-001）会被拒。
    用 UUID5 从 content_id 稳定派生，保证幂等（同内容 → 同点）。
    """
    return str(uuid.uuid5(uuid.NAMESPACE_URL, content_id))


class VectorStore:
    """Qdrant 封装（进程内单例）"""

    def __init__(self, url: str | None = None):
        # 修复：Qdrant 地址从配置读取（原硬编码 localhost:6333）
        self.client = QdrantClient(url=url or settings.qdrant_url)
        self._ensure_collection()

    def _ensure_collection(self) -> None:
        """建生效的默认 collection（幂等）：named vectors text_vec + 预留 image_vec

        TD-P1C（2026-08-26）：默认库经 default_collection() 解析——
        测试环境（QDRANT_COLLECTION=test_*）不建/不写生产 yishu_contents。
        """
        self.ensure_collection(default_collection())

    def ensure_collection(self, name: str) -> None:
        """按需建 collection（幂等；基准评测用独立 collection 隔离生产检索空间）"""
        existing = {c.name for c in self.client.get_collections().collections}
        if name in existing:
            return
        self.client.create_collection(
            collection_name=name,
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
        logger.info("Qdrant collection 已创建: %s", name)

    def _upsert_merged(
        self,
        col: str,
        pid: str,
        vectors: dict,
        payload: dict,
    ) -> None:
        """整点替换前合并已有向量/payload（Qdrant upsert 是 replace 语义）

        text_vec/text_sparse/image_vec 分属不同写入路径，若各自只带自己的向量
        整点 upsert 会把对方冲掉——统一先 retrieve 读回再合并，payload 强制带
        content_id（检索回填真实内容 id，避免退化 point_id 哈希）。
        """
        merged_vectors: dict = dict(vectors)
        merged_payload = dict(payload)
        existing = self.client.retrieve(collection_name=col, ids=[pid], with_vectors=True)
        if existing and existing[0].vector:
            for name, v in existing[0].vector.items():
                if name not in merged_vectors:
                    merged_vectors[name] = v
            if existing[0].payload:
                merged_payload = {**existing[0].payload, **merged_payload}
        self.client.upsert(
            collection_name=col,
            points=[models.PointStruct(id=pid, vector=merged_vectors, payload=merged_payload)],
        )

    def update_payload(
        self,
        content_id: str,
        payload: dict,
        collection: str | None = None,
    ) -> None:
        """轻量更新 payload（不触碰向量；2026-08-26 集成）

        用途：photo 首入库时 place/ci_tags 在索引后补全（pipeline.py 逆地理/CI
        打标之后刷新一次），避免重新编码 embedding。点不存在时 Qdrant 静默忽略。
        """
        col = collection or default_collection()
        self.ensure_collection(col)
        self.client.set_payload(
            collection_name=col,
            payload=payload,
            points=[point_id_for(content_id)],
        )

    def upsert_content(
        self,
        content_id: str,
        text: str,
        dense: list[float],
        sparse: dict[str, float],
        payload: dict,
        collection: str | None = None,
    ) -> None:
        """写入内容向量（text_vec dense + text_sparse）

        collection：指定目标 collection（基准评测 → yishu_benchmark，不污染生产）。
        """
        col = collection or default_collection()
        self.ensure_collection(col)
        self._upsert_merged(
            col,
            point_id_for(content_id),
            {
                DENSE_VEC_NAME: dense,
                "text_sparse": models.SparseVector(
                    indices=list(sparse.keys()),
                    values=list(sparse.values()),
                ),
            },
            {"content_id": content_id, **payload},
        )

    def search(
        self,
        dense: list[float],
        sparse: dict[str, float],
        filters: dict | None = None,
        limit: int = 50,
        collection: str | None = None,
    ) -> list[dict]:
        """混合检索：dense + sparse 并行召回 → RRF 融合（WeKnora 0.7/0.3, k=60）

        collection：检索目标 collection（默认生产；基准评测传 yishu_benchmark）。
        """
        col = collection or default_collection()
        # dense 路（query_points + using 指定 named vector；qdrant-client 1.19）
        dense_hits = self.client.query_points(
            collection_name=col,
            query=dense,
            using=DENSE_VEC_NAME,
            query_filter=self._to_filter(filters),
            limit=limit,
        ).points

        # sparse 路
        sparse_hits = []
        if sparse:
            sparse_hits = self.client.query_points(
                collection_name=col,
                query=models.SparseVector(
                    indices=list(sparse.keys()),
                    values=list(sparse.values()),
                ),
                using="text_sparse",
                query_filter=self._to_filter(filters),
                limit=limit,
            ).points

        return self._rrf_fuse(dense_hits, sparse_hits, limit=limit)

    def upsert_image_vec(
        self,
        content_id: str,
        vec: list[float],
        payload: dict | None = None,
        collection: str | None = None,
    ) -> None:
        """写入图片向量（image_vec 命名向量；B2-4 以图搜图）

        图片语义向量 = BGE-M3(caption)（caption 向量化方案，B2-4 允许的替代路径；
        tongyi-embedding-vision-plus 待账号开通后可零切换替换）。

        与 upsert_content 同点共存：Qdrant upsert 是整点替换，若只带 image_vec
        会把已有 text_vec/text_sparse 冲掉（反之亦然）——这里先读回已有向量合并后
        一次写入；payload 强制写 content_id，供 search_image 回填真实内容 id
        （否则检索结果退化为 point_id 哈希，无法溯源）。
        """
        col = collection or default_collection()
        self.ensure_collection(col)
        self._upsert_merged(
            col,
            point_id_for(content_id),
            {IMAGE_VEC_NAME: vec},
            {"content_id": content_id, **(payload or {})},
        )

    def search_image(
        self,
        vec: list[float],
        filters: dict | None = None,
        limit: int = 50,
        collection: str | None = None,
    ) -> list[dict]:
        """以图搜图：image_vec 相似检索（图片查询向量 → 最相似图片）"""
        col = collection or default_collection()
        hits = self.client.query_points(
            collection_name=col,
            query=vec,
            using=IMAGE_VEC_NAME,
            query_filter=self._to_filter(filters),
            limit=limit,
        ).points
        result = []
        for hit in hits:
            payload = hit.payload or {}
            result.append({
                "content_id": str(payload.get("content_id", hit.id)),
                "score": round(float(hit.score), 4),
                "dense_score": round(float(hit.score), 4),
                "sparse_score": 0.0,
                "text": payload.get("text"),
            })
        return result

    @staticmethod
    def _rrf_fuse(dense_hits, sparse_hits, limit: int = 50, k: int = 60) -> list[dict]:
        """RRF 融合（Reciprocal Rank Fusion）"""
        scores: dict[str, float] = {}
        details: dict[str, dict] = {}

        for rank, hit in enumerate(dense_hits, 1):
            pid = str(hit.payload.get("content_id", hit.id)) if hit.payload else str(hit.id)
            scores[pid] = scores.get(pid, 0.0) + 0.7 / (k + rank)
            details.setdefault(pid, {"dense_score": hit.score, "sparse_score": 0.0, "text": None})
            details[pid]["dense_score"] = hit.score
            if hit.payload:
                details[pid]["text"] = hit.payload.get("text")

        for rank, hit in enumerate(sparse_hits, 1):
            pid = str(hit.payload.get("content_id", hit.id)) if hit.payload else str(hit.id)
            scores[pid] = scores.get(pid, 0.0) + 0.3 / (k + rank)
            d = details.setdefault(pid, {"dense_score": 0.0, "sparse_score": 0.0, "text": None})
            d["sparse_score"] = hit.score
            if hit.payload:
                d["text"] = hit.payload.get("text")

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
        result = []
        for pid, score in ranked:
            d = details[pid]
            result.append({
                "content_id": pid,
                "score": round(score, 4),
                "dense_score": round(d["dense_score"], 4),
                "sparse_score": round(d["sparse_score"], 4),
                "text": d.get("text"),
            })
        return result

    @staticmethod
    def _to_filter(filters: dict | None) -> models.Filter | None:
        """payload filter（时间/类型/地点/实体 tag，B2-2：过滤层不是召回路）

        Qdrant Range 只接受数值——datetime 统一转 epoch 秒（int），
        payload 侧 taken_at 也应以 epoch 秒存储（与 ISO 字符串互斥）。
        """
        if not filters:
            return None
        must: list = []
        for key, value in filters.items():
            if key == "content_types" and value:
                # FIX-1（2026-08-26）：过滤值归一——"image" 别名映射为规范值 "photo"，
                # 且请求 "photo" 时同时匹配遗留 "image" 点（旧数据不丢），
                # 生产 photo 点（payload "photo"）与基准旧点（payload "image"）均可命中。
                any_vals: list[str] = []
                for v in value:
                    norm = CONTENT_TYPE_ALIASES.get(v, v)
                    if norm not in any_vals:
                        any_vals.append(norm)
                    if norm == CONTENT_TYPE_PHOTO and CONTENT_TYPE_IMAGE_LEGACY not in any_vals:
                        any_vals.append(CONTENT_TYPE_IMAGE_LEGACY)
                must.append(models.FieldCondition(
                    key="content_type",
                    match=models.MatchAny(any=any_vals),
                ))
            elif key == "time_from" and value:
                must.append(models.FieldCondition(
                    key="taken_at",
                    range=models.Range(gte=value.timestamp()),
                ))
            elif key == "time_to" and value:
                must.append(models.FieldCondition(
                    key="taken_at",
                    range=models.Range(lte=value.timestamp()),
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
            elif key == "content_class" and value:
                # P1-A 类目路由（2026-08-25）：按类别过滤（生产 payload 字段
                # content_class；基准 collection 的 _index 同步写入同名字段）。
                must.append(models.FieldCondition(
                    key="content_class",
                    match=models.MatchValue(value=value),
                ))
            elif key == "user_id" and value:
                # 用户隔离（2026-08-26 修复）：检索阶段即按 user_id 过滤，
                # 防跨用户内容挤占召回窗口（此前仅溯源回填隔离，
                # 数据多时新用户内容被挤出 top-k，api_smoke 门禁暴露）。
                must.append(models.FieldCondition(
                    key="user_id",
                    match=models.MatchValue(value=str(value)),
                ))
        return models.Filter(must=must) if must else None


@lru_cache(maxsize=1)
def get_store() -> VectorStore:
    """获取单例（lru_cache 缓存实例，延迟初始化）"""
    return VectorStore()


@lru_cache(maxsize=1)
def get_qdrant_client() -> QdrantClient:
    """Qdrant 客户端统一单例（P2-04 收敛：correction/vector_store 共用同一连接，
    消除 correction._correction_store 手写 global 的第二套单例）"""
    return QdrantClient(url=settings.qdrant_url)
