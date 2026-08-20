"""RAG 检索服务（B2 设计：混合检索 + 路由分层）

管线（B2 4.1）：
  query → Query 改写（LLM：时间表达/实体抽取）→ 路由（文本/图片/混合）
  → dense+sparse 双路召回（Qdrant RRF）→ payload filter
  → bge-reranker 粗排（占位：M1 后接）→ 溯源

Mock 说明：未配置 DASHSCOPE_API_KEY 时，改写/路由走规则兜底（可测可联调），
M1 门禁（Top3≥70% + P95<3s）以规则兜底版本为基线。
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.db.models import Content
from app.schemas.search import SearchHit, SearchQuery, SearchResult
from app.services.embedding import encode_query
from app.services.external import rewrite_query, route_query
from app.services.ner import extract_entities
from app.services.rerank import rerank
from app.services.vector_store import get_store

logger = logging.getLogger("yishu.rag")

# P2-01 并发上限：搜索保留同步（P95<3s 门禁依赖本地推理），但用信号量限制并发
# 推理数，防止 BGE-M3(1.2GB)/reranker 同时加载打满内存、线程池被推理占满。
SEARCH_CONCURRENCY = 4
_search_semaphore = threading.BoundedSemaphore(SEARCH_CONCURRENCY)

# 时间表达规则（"去年夏天" → 时间范围；MVP 简化）
_TIME_PATTERNS = [
    (re.compile(r"去年"), "last_year"),
    (re.compile(r"今年"), "this_year"),
    (re.compile(r"上个月"), "last_month"),
    (re.compile(r"昨天"), "yesterday"),
]


def _rewrite_query(q: SearchQuery) -> tuple[str, dict, dict]:
    """Query 改写（RET-007）：时间表达解析 → 过滤条件；无 LLM 时规则兜底

    返回 (rewritten, filters, ner_filters)：ner_filters 为 NER 派生过滤子集
    （place/tag），供搜索层"空结果回退"用——避免语料缺元数据时过过滤。
    """
    filters: dict = {}
    rewritten = q.q
    now = datetime.now(timezone.utc).astimezone()

    for pattern, kind in _TIME_PATTERNS:
        if pattern.search(q.q):
            if kind == "last_year":
                filters["time_from"] = now.replace(year=now.year - 1, month=1, day=1)
                filters["time_to"] = now.replace(year=now.year - 1, month=12, day=31)
            elif kind == "this_year":
                filters["time_from"] = now.replace(month=1, day=1)
            elif kind == "last_month":
                first = now.replace(day=1)
                if first.month > 1:
                    prev = first.replace(month=first.month - 1)
                else:
                    prev = first.replace(year=first.year - 1, month=12)
                filters["time_from"] = prev
                filters["time_to"] = first
            elif kind == "yesterday":
                y = now - timedelta(days=1)
                filters["time_from"] = y.replace(hour=0, minute=0, second=0)
                filters["time_to"] = y.replace(hour=23, minute=59, second=59)
            rewritten = pattern.sub("", q.q).strip()
            break

    # LLM 改写（S1-03 百炼接入：配置 key 后启用；未配置/失败 → 规则结果兜底）
    # 修复（审查 MINOR）：LLM 输入用规则改写后的文本（rewritten），避免
    # LLM 把已删的时间词带回原文（filter 已生效，文本却含"去年"造成语义噪音）
    try:
        llm_q = rewrite_query(rewritten)
        if llm_q:
            rewritten = llm_q
    except RuntimeError:
        pass

    if q.content_types:
        filters["content_types"] = q.content_types
    if q.time_from:
        filters["time_from"] = q.time_from
    if q.time_to:
        filters["time_to"] = q.time_to
    if q.place:
        filters["place"] = q.place
    if q.tag:
        filters["tag"] = q.tag

    # B2-2 NER 实体抽取（2026-08-19）：查询"苏州"/"小张" → place/tag 过滤
    # 规则词表起步（零延迟零成本，P95<3s 门禁不增 LLM 往返）；LLM 兜底默认关，
    # 待延迟预算评估后开（enable_llm=True）。显式参数优先于 NER 抽取结果。
    entities = extract_entities(rewritten)
    ner_filters: dict = {}
    if entities.get("place") and not filters.get("place"):
        filters["place"] = entities["place"]
        ner_filters["place"] = entities["place"]
    if entities.get("person") and not filters.get("tag"):
        filters["tag"] = entities["person"]
        ner_filters["tag"] = entities["person"]

    return rewritten, filters, ner_filters


def _route_query(q: str) -> str:
    """查询路由（B2：文本/图片/混合意图；LLM 优先，未配置时规则兜底）"""
    try:
        return route_query(q)
    except RuntimeError:
        pass
    # 规则兜底：图片意图关键词（B2 路由；词表增强 WP-F：扩充到常见图片表达）
    image_hints = [
        "照片", "图片", "拍的", "截图", "这张", "图里", "壁纸", "表情包",
        "相册", "抓拍", "合照", "自拍", "风景照", "图片里", "照片里",
    ]
    if any(h in q for h in image_hints):
        return "image"
    return "text"


def _merge_recalls(recalls: list[list[dict]], limit: int = 50) -> list[dict]:
    """多路召回合并：按 content_id 去重保留最高分（mixed 双路融合用）"""
    merged: dict[str, dict] = {}
    for hits in recalls:
        for hit in hits:
            cid = hit["content_id"]
            if cid not in merged or hit["score"] > merged[cid]["score"]:
                merged[cid] = hit
    return sorted(merged.values(), key=lambda x: x["score"], reverse=True)[:limit]


def _assemble_hits(raw_hits: list[dict], limit: int, db, user_id: str | None) -> list[SearchHit]:
    """溯源组装（RET-016：每条结果可解释命中字段；按用户隔离回填真实内容）"""
    hits: list[SearchHit] = []
    if not raw_hits:
        return hits
    content_ids = [rh["content_id"] for rh in raw_hits[:limit]]
    content_map: dict[str, Content] = {}
    if db is not None and user_id is not None:
        # 过滤非 UUID 格式 id（UUID 列无法匹配 rag-001 类测试点，防 PG 报错）
        _uuid_re = re.compile(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        )
        valid_ids = [cid for cid in content_ids if _uuid_re.match(cid)]
        if valid_ids:
            rows = db.execute(
                select(Content).where(
                    Content.id.in_(valid_ids),
                    Content.user_id == user_id,
                )
            ).scalars().all()
            content_map = {str(c.id): c for c in rows}
    for rh in raw_hits[:limit]:
        c = content_map.get(rh["content_id"])
        matched = []
        if rh["dense_score"] > 0:
            matched.append("dense")
        if rh["sparse_score"] > 0:
            matched.append("sparse")
        hits.append(SearchHit(
            content_id=rh["content_id"],
            content_type=c.content_type if c else "text",
            text=(c.text if c else None) or rh.get("text"),
            taken_at=c.taken_at if c else None,
            place=c.place if c else None,
            event_id=None,
            event_title=None,
            score=rh["score"],
            trace={
                "matched": matched or ["dense"],
                "dense_score": rh["dense_score"],
                "sparse_score": rh["sparse_score"],
                "rrf": rh["score"],
            },
        ))
    return hits


def search(q: SearchQuery, db=None, user_id: str | None = None, collection: str | None = None) -> SearchResult:
    """描述性搜索主链路（API-003/RET-001~018 前置）

    user_id：溯源回填 contents 时按用户隔离（修复：原占位文本无真实内容）。
    collection：检索目标 collection（默认生产 yishu_contents；RAG 基准评测传
    yishu_benchmark 独立库，避免基准数据混入真实检索结果）。

    P2-01：并发上限（信号量阻塞排队，不放任线程池被推理占满）。
    """
    with _search_semaphore:
        return _search_impl(q, db=db, user_id=user_id, collection=collection)


def _search_impl(q: SearchQuery, db=None, user_id: str | None = None, collection: str | None = None) -> SearchResult:
    """搜索实现体（被 search 信号量包裹）"""
    start = time.perf_counter()
    degraded = False

    # 1. Query 改写 → 过滤条件（返回 NER 派生过滤，供空结果回退）
    rewritten, filters, ner_filters = _rewrite_query(q)

    # 2. 路由（B2：路由决定检索范围——image 意图只搜图片 caption，文字搜图）
    intent = _route_query(rewritten)
    if intent == "image":
        filters.setdefault("content_types", ["image"])

    # 3. 编码 + 召回
    try:
        dense, sparse = encode_query(rewritten)
        store = get_store()
        if intent == "mixed":
            # B2 mixed 双路融合（2026-08-19）：image 路 + 全量路并行召回 → 去重合并
            image_filters = {**filters, "content_types": ["image"]}
            raw_hits = _merge_recalls([
                store.search(dense, sparse, filters=image_filters, limit=50, collection=collection),
                store.search(dense, sparse, filters=filters, limit=50, collection=collection),
            ], limit=50)
        else:
            raw_hits = store.search(dense, sparse, filters=filters, limit=50, collection=collection)
        # 空结果回退（2026-08-19）：仅 NER 派生过滤导致空结果时，去掉 NER 过滤重试
        # （语料缺 place/tags 元数据时防"过过滤空结果"；显式参数仍是硬约束）
        if not raw_hits and ner_filters:
            retry_filters = {k: v for k, v in filters.items() if k not in ner_filters}
            logger.info("NER 过滤空结果，回退重试（去掉 %s）", list(ner_filters))
            if intent == "mixed":
                image_filters = {**retry_filters, "content_types": ["image"]}
                raw_hits = _merge_recalls([
                    store.search(dense, sparse, filters=image_filters, limit=50, collection=collection),
                    store.search(dense, sparse, filters=retry_filters, limit=50, collection=collection),
                ], limit=50)
            else:
                raw_hits = store.search(dense, sparse, filters=retry_filters, limit=50, collection=collection)
    except Exception as exc:  # noqa: BLE001 —— Qdrant 不可用降级（API-009）
        logger.warning("Qdrant 检索降级: %s", exc)
        degraded = True
        raw_hits = []

    # 4. 溯源组装（RET-016：每条结果可解释命中字段）
    hits = _assemble_hits(raw_hits, q.limit, db, user_id)

    latency_ms = int((time.perf_counter() - start) * 1000)

    # 4.5 双层 Rerank 第一层（bge-reranker 粗排 + 低相关过滤；
    #      候选无 text/模型未就绪 → 原序）
    if hits:
        hits = [c["hit"] for c in rerank(
            rewritten,
            [{"id": h.content_id, "text": h.text or "", "score": h.score, "hit": h} for h in hits],
            min_score=settings.rerank_min_score,
        )][: q.limit]

    return SearchResult(
        query=q.q,
        rewritten_query=rewritten if rewritten != q.q else None,
        intent=intent,
        hits=hits,
        total=len(hits),
        latency_ms=latency_ms,
        degraded=degraded,
    )


def search_by_image(
    image_path: str,
    q: SearchQuery,
    db=None,
    user_id: str | None = None,
    collection: str | None = None,
) -> SearchResult:
    """以图搜图（B2-4 · 2026-08-19）：图片 → Qwen3-VL caption → BGE-M3 向量 → image_vec 检索

    P2-01：与 search 共享并发信号量（Qwen3-VL + 编码均为重推理）。
    """
    with _search_semaphore:
        return _search_by_image_impl(image_path, q, db=db, user_id=user_id, collection=collection)


def _search_by_image_impl(
    image_path: str,
    q: SearchQuery,
    db=None,
    user_id: str | None = None,
    collection: str | None = None,
) -> SearchResult:
    """以图搜图实现体（被 search_by_image 信号量包裹）

    caption 向量化方案（B2-4 允许的替代路径；tongyi-embedding-vision-plus 开通后可替换）。
    返回结构同描述性搜索（intent=image）。
    """
    from app.services.embedding import encode_dense
    from app.services.external.dashscope import image_caption

    start = time.perf_counter()
    degraded = False
    try:
        caption = image_caption(image_path)
        vec = encode_dense([caption])[0]
        store = get_store()
        raw_hits = store.search_image(vec, filters={"content_types": ["image"]}, limit=50, collection=collection)
    except Exception as exc:  # noqa: BLE001 —— 图片塔/向量库不可用降级
        logger.warning("以图搜图降级: %s", exc)
        degraded = True
        caption = ""
        raw_hits = []

    hits = _assemble_hits(raw_hits, q.limit, db, user_id)
    latency_ms = int((time.perf_counter() - start) * 1000)
    return SearchResult(
        query=q.q,
        rewritten_query=caption or None,
        intent="image",
        hits=hits,
        total=len(hits),
        latency_ms=latency_ms,
        degraded=degraded,
    )
