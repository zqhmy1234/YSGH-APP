"""sensitive.py —— B5b 护栏域钩子：事件级敏感标记

任务归属：Wave 1 Agent C（B5b 护栏域）独占本文件。

mark_sensitive_on_ingest 流程（pipeline.py 已在入库路径调用）：
1. 规则词表先行：external/sensitive_words.check_event_sensitive（7 类事件词表）
   + sensitive_words 表回流词（level 2/3：全局 + 用户级，DB 持久层）
2. 规则未命中 → llm_ops/guard.detect_event_sensitive（qwen-flash 补漏，
   抓"他说以后别联系了"类表达；mock/未配 key → [] 静默降级）
3. 敏感有效期：事件级带 detected_at 时间戳 + 最近提及计数（用户主动提及 +1），
   某话题累计 ≥3 次 → 降级普通话题（sensitive_status 回"正常"、tags 记 downgraded，
   历史内容同步降级——计数含已降级内容，降级后不再反复横跳）；
   画像级（profile_sensitive，locked 永不过期）不走本钩子，见 echo 双查 L1 校验。
4. 命中 → 写 contents.sensitive_tags + sensitive_status="敏感"（内容正常入库，
   只影响回响/追问等主动提及路径）。
5. 违规词回流：LLM 判敏感且规则未覆盖 → guard.reflow_llm_categories 写
   SensitiveWord(level=3) 自动入规则表 + 进程内热加入。
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.db.models import Content, SensitiveWord
from app.services.external.sensitive_words import (
    add_violation_word,
    check_event_sensitive,
)
from app.services.llm_ops.guard import (
    detect_event_sensitive,
    reflow_llm_categories,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger("yishu.sensitive_ext")

# 降级阈值：用户主动提及同一敏感话题 ≥3 次 → 降级普通话题（产品部口径）
DOWNGRADE_MENTION_THRESHOLD = 3


def _tags_has_category(tags: dict | None, category: str) -> bool:
    """sensitive_tags JSON 是否含指定类别（容错：tags 可能为 None/缺 categories）"""
    if not tags:
        return False
    return category in (tags.get("categories") or [])


def _mention_count(db: Session, user_id: str, category: str, exclude_content_id: str) -> int:
    """最近提及计数：用户已入库内容中带该敏感类别的条数（排除当前内容自身）"""
    cands = db.execute(
        select(Content.id, Content.sensitive_tags).where(
            Content.user_id == user_id,
            Content.deleted_at.is_(None),
            Content.id != exclude_content_id,
            Content.sensitive_tags.isnot(None),
        )
    ).all()
    return sum(1 for _, tags in cands if _tags_has_category(tags, category))


def _downgrade_existing(db: Session, user_id: str, category: str, exclude_content_id: str) -> None:
    """话题降级：历史带该类别的敏感内容 → sensitive_status 回"正常" + tags 记 downgraded

    与当前内容分开处理（当前内容由调用方统一落 tags/status），保持幂等。
    """
    cands = db.execute(
        select(Content).where(
            Content.user_id == user_id,
            Content.deleted_at.is_(None),
            Content.id != exclude_content_id,
            Content.sensitive_tags.isnot(None),
        )
    ).scalars().all()
    for c in cands:
        if _tags_has_category(c.sensitive_tags, category):
            tags = dict(c.sensitive_tags or {})
            tags["downgraded"] = True
            c.sensitive_tags = tags
            c.sensitive_status = "正常"


def _db_event_words(db: Session, user_id: str, text: str) -> list[str]:
    """sensitive_words 表回流词（level 2/3：全局 + 用户级）→ 命中词列表

    DB 表是回流词持久层（文件词表为 level-1 预置）。命中词归入"回流词"类别；
    同时热加入进程内规则集（add_violation_word），同进程后续直接文件规则命中。
    """
    rows = db.execute(
        select(SensitiveWord.word).where(
            (SensitiveWord.user_id == user_id) | (SensitiveWord.user_id.is_(None))
        )
    ).scalars().all()
    hits: list[str] = []
    for raw in rows:
        w = (raw or "").strip()
        if w and w in text:
            hits.append(w)
            add_violation_word(w)
    return hits


def _detected_at() -> str:
    return datetime.now().astimezone().isoformat()


def mark_sensitive_on_ingest(db: Session, content: Content) -> None:
    """事件级敏感分类 + 标记（规则先行 → LLM 补漏 → 有效期/降级 → 回流）"""
    text = (content.text or "").strip()
    if not text:
        return
    user_id = str(content.user_id)

    # 1. 规则层：文件事件词表 + DB 回流词
    rule = check_event_sensitive(text)
    categories = list(rule["categories"])
    matched = list(rule["matched"])
    for w in _db_event_words(db, user_id, text):
        if w not in matched:
            matched.append(w)
        if "回流词" not in categories:
            categories.append("回流词")
    source = "rule"

    # 2. LLM 补漏（规则未命中 → qwen-flash；mock 模式返回 []）
    if not categories:
        llm_cats = detect_event_sensitive(text)
        if llm_cats:
            categories = llm_cats
            matched = []
            source = "llm"
            # 违规词回流：LLM 判敏感且规则未覆盖 → 类别种子词入规则表
            reflow_llm_categories(db, llm_cats)

    # 3. 敏感有效期：提及计数 + 降级（≥3 次 → 普通话题；画像级 locked 永不过期，
    #    不走本钩子——见 echo.py 画像 L1 校验）
    kept: list[str] = []
    downgraded: list[str] = []
    counts: dict[str, int] = {}
    for cat in categories:
        cnt = _mention_count(db, user_id, cat, content.id) + 1  # 本次主动提及 +1
        counts[cat] = cnt
        if cnt >= DOWNGRADE_MENTION_THRESHOLD:
            downgraded.append(cat)
        else:
            kept.append(cat)

    # 4. 写标记
    now = _detected_at()
    if kept:
        content.sensitive_tags = {
            "categories": kept,
            "matched": matched,
            "source": source,
            "detected_at": now,
            "mention_count": max(counts[c] for c in kept),
            "downgraded": False,
        }
        content.sensitive_status = "敏感"
    elif downgraded:
        # 本次内容：话题已降级 → 不标敏感（保持"正常"），并降级历史内容
        for cat in downgraded:
            _downgrade_existing(db, user_id, cat, content.id)
        content.sensitive_tags = {
            "categories": downgraded,
            "matched": matched,
            "source": source,
            "detected_at": now,
            "mention_count": max(counts[c] for c in downgraded),
            "downgraded": True,
        }
        content.sensitive_status = "正常"
