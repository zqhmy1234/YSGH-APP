"""Qdrant 降级 → PG 全文检索兜底（audit #16 / API-009 降级不再空结果）

拆包自 services/rag.py（F6，2026-08-27）：Qdrant 不可用时 ILIKE 多词 OR +
命中词元数排序（确定性、零依赖、mock 可用）；返回结构与向量检索 hit 同构
（多带 "pg" 标记，trace 显示真实通道）。
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime

from sqlalchemy import String, cast, or_, select

from app.db.models import Content, Event, EventItem
from app.schemas.search import SearchQuery
from app.services.query_helpers import alive_clause
from app.services.search_filters import (
    HANDLED_BY_PG,
    assert_full_coverage,
    normalize_filters,
)

logger = logging.getLogger("yishu.rag")


def _query_tokens(text: str) -> list[str]:
    """关键词归一：保留中文单字，英文/数字至少 2 字符。"""
    raw = re.split(r"[\s,，。.！!？?、；;:：（）()「」『』【】\"'‘’]", text or "")
    return [
        token
        for token in raw
        if len(token) >= 2 or re.search(r"[\u4e00-\u9fff]", token)
    ]


def _like_pattern(token: str) -> str:
    """ILIKE 通配符转义，用户输入的 %/_ 不得扩成全库匹配。"""
    escaped = token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _pg_exact_search(
    q: SearchQuery,
    db,
    user_id: str | None,
    limit: int = 50,
) -> list[dict]:
    """独立精确关键词召回：内容原文 / 标签 / 事件标题。

    这条路与 Qdrant 是否返回噪声候选无关，调用方必须始终合并。只应用
    SearchQuery 的显式硬过滤，不套用 LLM/NER/分类器推断条件，避免正确
    关键词被模型推断的类别或实体误过滤。
    """
    if db is None or user_id is None:
        return []
    tokens = _query_tokens(q.q)
    if not tokens:
        return []

    patterns = [_like_pattern(t) for t in tokens]
    content_text_match = or_(
        *[Content.text.ilike(pattern, escape="\\") for pattern in patterns]
    )
    tag_text_match = or_(
        *[cast(Content.tags_json, String).ilike(pattern, escape="\\") for pattern in patterns]
    )
    ci_tag_match = or_(
        *[Content.extra["ci_tags"].astext.ilike(pattern, escape="\\") for pattern in patterns]
    )

    stmt = select(Content).where(
        Content.user_id == str(user_id),
        alive_clause(Content),
        Content.status == "done",
        or_(content_text_match, tag_text_match, ci_tag_match),
    )
    if q.content_types:
        from app.services.vector_store import CONTENT_TYPE_ALIASES

        stmt = stmt.where(
            Content.content_type.in_(
                [CONTENT_TYPE_ALIASES.get(c, c) for c in q.content_types]
            )
        )
    if q.time_from is not None:
        stmt = stmt.where(Content.taken_at >= q.time_from)
    if q.time_to is not None:
        stmt = stmt.where(Content.taken_at <= q.time_to)
    if q.place:
        stmt = stmt.where(Content.place == q.place)
    if q.tag:
        stmt = stmt.where(Content.extra["ci_tags"].astext.ilike(_like_pattern(q.tag), escape="\\"))

    try:
        content_rows = db.execute(stmt).scalars().all()
        title_stmt = (
            select(EventItem.content_id, Event.title)
            .join(Event, Event.id == EventItem.event_id)
            .where(
                Event.user_id == str(user_id),
                alive_clause(Event),
                or_(*[Event.title.ilike(p, escape="\\") for p in patterns]),
            )
        )
        event_rows = db.execute(title_stmt).all()
        event_title_by_content = {
            str(row.content_id): row.title or "" for row in event_rows
        }
        if event_title_by_content:
            event_content_stmt = select(Content).where(
                Content.id.in_(list(event_title_by_content)),
                Content.user_id == str(user_id),
                alive_clause(Content),
                Content.status == "done",
            )
            if q.content_types:
                event_content_stmt = event_content_stmt.where(
                    Content.content_type.in_(
                        [CONTENT_TYPE_ALIASES.get(c, c) for c in q.content_types]
                    )
                )
            if q.time_from is not None:
                event_content_stmt = event_content_stmt.where(Content.taken_at >= q.time_from)
            if q.time_to is not None:
                event_content_stmt = event_content_stmt.where(Content.taken_at <= q.time_to)
            if q.place:
                event_content_stmt = event_content_stmt.where(Content.place == q.place)
            if q.tag:
                event_content_stmt = event_content_stmt.where(
                    Content.extra["ci_tags"].astext.ilike(_like_pattern(q.tag), escape="\\")
                )
            content_rows.extend(db.execute(event_content_stmt).scalars().all())
    except Exception:  # noqa: BLE001 —— 精确通道失败返回空，语义主路照常
        logger.warning("PG 精确关键词召回失败", exc_info=True)
        return []

    by_id: dict[str, Content] = {str(c.id): c for c in content_rows}
    ranked: list[tuple[float, str, Content, list[str]]] = []
    phrase = (q.q or "").strip()
    for cid, content in by_id.items():
        text = content.text or ""
        tag_blob = str(content.tags_json or "") + " " + str((content.extra or {}).get("ci_tags") or "")
        title = event_title_by_content.get(cid, "")
        matched_fields: list[str] = []
        score = 0.0

        text_hit = [t for t in tokens if t.lower() in text.lower()]
        if phrase and phrase.lower() in text.lower():
            score = 1.0
            matched_fields.append("text_phrase")
        elif text_hit:
            ratio = len(text_hit) / len(tokens)
            score = 0.80 + 0.15 * ratio
            matched_fields.append("text")

        tag_hit = [t for t in tokens if t.lower() in tag_blob.lower()]
        if tag_hit:
            score = max(score, 0.60 + 0.10 * (len(tag_hit) / len(tokens)))
            matched_fields.append("tag")

        title_hit = [t for t in tokens if t.lower() in title.lower()]
        if title_hit:
            score = max(score, 0.45 + 0.10 * (len(title_hit) / len(tokens)))
            matched_fields.append("event_title")

        if matched_fields:
            ranked.append((round(score, 4), cid, content, matched_fields))

    ranked.sort(
        key=lambda item: (
            item[0],
            item[2].taken_at or datetime.min.replace(tzinfo=UTC),
        ),
        reverse=True,
    )
    return [
        {
            "content_id": cid,
            "score": score,
            "dense_score": 0.0,
            "sparse_score": 0.0,
            "text": content.text,
            "pg": True,
            "exact_score": score,
            "matched_fields": fields,
        }
        for score, cid, content, fields in ranked[:limit]
    ]


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
        alive_clause(Content),
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
        return (sum(1 for t in tokens if t in text), c.taken_at or datetime.min.replace(tzinfo=UTC))

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
