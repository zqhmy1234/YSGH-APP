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
import time
from datetime import datetime, timedelta, timezone

from app.schemas.search import SearchHit, SearchQuery, SearchResult
from app.services.embedding import encode_query
from app.services.external import rewrite_query, route_query
from app.services.vector_store import get_store

logger = logging.getLogger("yishu.rag")

# 时间表达规则（"去年夏天" → 时间范围；MVP 简化）
_TIME_PATTERNS = [
    (re.compile(r"去年"), "last_year"),
    (re.compile(r"今年"), "this_year"),
    (re.compile(r"上个月"), "last_month"),
    (re.compile(r"昨天"), "yesterday"),
]


def _rewrite_query(q: SearchQuery) -> tuple[str, dict]:
    """Query 改写（RET-007）：时间表达解析 → 过滤条件；无 LLM 时规则兜底"""
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
    try:
        llm_q = rewrite_query(q.q)
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

    return rewritten, filters


def _route_query(q: str) -> str:
    """查询路由（B2：文本/图片/混合意图；LLM 优先，未配置时规则兜底）"""
    try:
        return route_query(q)
    except RuntimeError:
        pass
    # 规则兜底：图片意图关键词（"照片里""拍的""这张图"）
    image_hints = ["照片", "图片", "拍的", "截图", "这张", "图里"]
    if any(h in q for h in image_hints):
        return "image"
    return "text"


def search(q: SearchQuery, db=None) -> SearchResult:
    """描述性搜索主链路（API-003/RET-001~018 前置）"""
    start = time.perf_counter()
    degraded = False

    # 1. Query 改写 → 过滤条件
    rewritten, filters = _rewrite_query(q)

    # 2. 路由
    intent = _route_query(rewritten)

    # 3. 编码 + 召回
    try:
        dense, sparse = encode_query(rewritten)
        store = get_store()
        raw_hits = store.search(dense, sparse, filters=filters, limit=50)
    except Exception as exc:  # noqa: BLE001 —— Qdrant 不可用降级（API-009）
        logger.warning("Qdrant 检索降级: %s", exc)
        degraded = True
        raw_hits = []

    # 4. 溯源组装（RET-016：每条结果可解释命中字段）
    hits = []
    for rh in raw_hits[: q.limit]:
        hits.append(SearchHit(
            content_id=rh["content_id"],
            content_type="text",
            text=f"<检索结果 {rh['content_id']}>",
            taken_at=None,
            place=None,
            event_id=None,
            event_title=None,
            score=rh["score"],
            trace={
                "matched": ["dense" if rh["dense_score"] > 0 else "sparse"],
                "dense_score": rh["dense_score"],
                "sparse_score": rh["sparse_score"],
                "rrf": rh["score"],
            },
        ))

    latency_ms = int((time.perf_counter() - start) * 1000)
    return SearchResult(
        query=q.q,
        rewritten_query=rewritten if rewritten != q.q else None,
        intent=intent,
        hits=hits,
        total=len(hits),
        latency_ms=latency_ms,
        degraded=degraded,
    )
