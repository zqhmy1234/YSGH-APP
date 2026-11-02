"""sensitive.py —— B5b 护栏域钩子：事件级敏感标记

任务归属：Wave 1 Agent C（B5b 护栏域）独占本文件。

mark_sensitive_on_ingest 流程（pipeline.py 已在入库路径调用）：
1. 规则词表先行：external/sensitive_words.check_event_sensitive（7 类事件词表）
   + sensitive_words 表回流词（level 2/3：全局 + 用户级，DB 持久层）
2. 规则未命中 → llm_ops/guard.detect_event_sensitive_status（qwen-flash 补漏，
   抓"他说以后别联系了"类表达）。**D10-9（2026-09-25 用户拍板「按建议来」）**：
   补漏**没跑通**（未配 key / 调用异常）时，不再当作"内容正常"静默放行，而是把
   内容标成 `sensitive_status="待复核"` ⇒ 主动提及路径（回响/推送）一律跳过它，
   **但入库与被动检索完全不受影响**。mock 模式（本地开发/测试）视为可用，
   避免把整库标成待复核。
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
    detect_event_sensitive_status,
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
    degraded = False  # D10-9：软话题检测"没跑通"（区别于"检出为空"）

    # 2. LLM 补漏（规则未命中 → qwen-flash；mock 模式视为可用）
    if not categories:
        llm_cats, detector_ok = detect_event_sensitive_status(text)
        if llm_cats:
            categories = llm_cats
            matched = []
            source = "llm"
            # 违规词回流：LLM 判敏感且规则未覆盖 → 类别种子词入规则表
            reflow_llm_categories(db, llm_cats)
        elif not detector_ok:
            degraded = True

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
    elif degraded:
        # D10-9（2026-09-25）：规则未命中 **且** 软话题检测没跑通 ⇒ **不主动提及**。
        #
        # 为什么不写"敏感"：我们并**没有**判定它敏感，只是"没能判定"——写成"敏感"
        # 会污染语义（且 30 天降级逻辑会把它当敏感话题计数）。"待复核" 精准表达
        # "未确认"，而 `echo._is_sensitive`（`!= "正常"`）与 `notify` 的主动提及
        # 过滤（`is_(None) | == "正常"`）都会跳过它 ⇒ 回响/推送不提，**入库与
        # 被动检索照常**（用户自己翻看不受影响）—— 这正是拍板选项 C 的口径。
        content.sensitive_tags = {
            "categories": [],
            "matched": [],
            "source": "degraded",
            "detected_at": now,
            "mention_count": 0,
            "downgraded": False,
            "note": "软话题检测不可用（未配 key 或调用失败），按不可主动提及处理",
        }
        content.sensitive_status = "待复核"
