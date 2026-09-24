"""Qdrant 降级 → PG 全文检索兜底（audit #16 / API-009 降级不再空结果）

拆包自 services/rag.py（F6，2026-08-27）：Qdrant 不可用时 ILIKE 多词 OR +
命中词元数排序（确定性、零依赖、mock 可用）；返回结构与向量检索 hit 同构
（多带 "pg" 标记，trace 显示真实通道）。
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import Content
from app.schemas.search import SearchQuery
from app.services.search_filters import (
    HANDLED_BY_PG,
    assert_full_coverage,
    normalize_filters,
)

logger = logging.getLogger("yishu.rag")


def _pg_fallback_search(
    q: SearchQuery,
    rewritten: str,
    filters: dict,
    db,
    user_id: str | None,
    limit: int,
) -> list[dict]:
    """Qdrant 降级 → PG 全文检索兜底（audit #16，API-009 降级不再空结果）

    中文无内置 tsvector parser（PG 默认分词按空格），用 ILIKE 多词 OR + 命中
    词元数排序（确定性、零依赖、mock 可用）。支持 content_type/时间/place/
    tag(ci_tags jsonb) 过滤与用户隔离；db/user_id 缺失（纯逻辑测试）返回 []。
    返回结构与向量检索 hit 同构（多带 "pg" 标记，trace 显示真实通道）。
    """
    if db is None or user_id is None:
        return []
    # D05-3（2026-09-25 · P0）：键集与未知键处置收敛到 services/search_filters.py
    #  ——未知键抛 UnknownFilterKey（此前逐个 filters.get 静默忽略：隔离键丢失
    #  即跨用户召回，且无任何日志）。
    filters = normalize_filters(filters)
    assert_full_coverage("rag.pg_fallback", HANDLED_BY_PG)
    tokens = [
        t for t in re.split(r"[\s,，。.！!？?、；;:：（）()「」『』【】\"'‘’]", rewritten or "")
        if len(t) >= 2
    ]
    if not tokens:
        return []
    from sqlalchemy import or_

    # 用户隔离：过滤器里的 user_id 与入参同源；两者都给时以**过滤器**为准
    # （隔离键必须是"我实际拿来过滤的那个值"，不能因为参数不同而被忽略 —— D05-3）
    effective_uid = str(filters.get("user_id") or user_id)
    stmt = select(Content).where(
        Content.user_id == effective_uid,
        Content.deleted_at.is_(None),
    )
    cts = filters.get("content_types")
    if cts:
        # FIX-1 同口径："image" 别名 → 规范 "photo"（生产 photo 点即 "photo"）
        from app.services.vector_store import CONTENT_TYPE_ALIASES

        stmt = stmt.where(Content.content_type.in_([CONTENT_TYPE_ALIASES.get(c, c) for c in cts]))
    if filters.get("content_class"):
        stmt = stmt.where(Content.content_class == filters["content_class"])
    if filters.get("time_from"):
        stmt = stmt.where(Content.taken_at >= filters["time_from"])
    if filters.get("time_to"):
        stmt = stmt.where(Content.taken_at <= filters["time_to"])
    if filters.get("place"):
        stmt = stmt.where(Content.place == filters["place"])
    if filters.get("tag"):
        # ci_tags 为 JSONB list[str]：cast to text 后 ILIKE（近似包含匹配）
        stmt = stmt.where(Content.extra["ci_tags"].astext.ilike(f"%{filters['tag']}%"))
    stmt = stmt.where(or_(*[Content.text.ilike(f"%{t}%") for t in tokens]))
    try:
        rows = db.execute(stmt).scalars().all()
    except Exception:  # noqa: BLE001 —— PG 兜底自身失败 → 空结果（不再抛）
        logger.warning("PG 兜底检索失败", exc_info=True)
        return []

    def _rank(c: Content) -> tuple[int, datetime]:
        text = c.text or ""
        return (sum(1 for t in tokens if t in text), c.taken_at or datetime.min.replace(tzinfo=timezone.utc))

    rows.sort(key=_rank, reverse=True)
    out = []
    for i, c in enumerate(rows[:limit]):
        out.append({
            "content_id": str(c.id),
            "score": round(max(0.0, 1.0 - i * 0.01), 4),
            "dense_score": 0.0,
            "sparse_score": 0.0,
            "text": c.text,
            "pg": True,
        })
    return out
