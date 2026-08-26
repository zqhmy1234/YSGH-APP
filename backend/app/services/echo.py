"""回响服务（P2-ECHO · 去年今日）

规则（产品部已确认）：
- 回响 = 去年今日的内容（照片/文字/语音），in-app 展示（非 push）
- 每天 ≤1 条（echo_history.shown_at 同日去重）；划掉（dismiss）不再出现
- 敏感排除（SAF-006/007 双查）：contents.sensitive_status ≠ 正常 跳过 +
  画像级敏感（profile_sensitive）命中跳过
- 指纹：内容 id 稳定派生（跨端同一条）

画像级敏感（B5b FIX-4）：本模块同时提供 profile_sensitive 表的服务函数
（回响 L1 校验 + B1-6 对话式增删查），供 /api/v1/profile/sensitive 使用。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Content, EchoHistory, ProfileSensitive

logger = logging.getLogger("yishu.echo")

ECHO_DAILY_LIMIT = 1

# 画像 L1 校验：命中这些处置级别 → 回响跳过不重提（产品口径；allow/mention 放行）
PROFILE_BLOCK_DISPOSITIONS = {"forbid", "caution", "review"}
PROFILE_DISPOSITIONS = {"allow", "mention", "caution", "review", "forbid"}


def _fingerprint(content_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"echo:{content_id}"))


def _local_now() -> datetime:
    """本地时间（日界按本地口径：修复审查 MINOR——原按 UTC 日界，本地 0:00-8:00
    会被算到前一天）"""
    return datetime.now().astimezone()


def profile_sensitive_blocked(db: Session, user_id: str, text: str) -> bool:
    """画像 L1 校验（B5b FIX-4）：查 profile_sensitive，命中 topic 且处置级别 ∈
    {forbid, caution, review} → 跳过不重提（allow/mention 放行）。

    画像级敏感永不过期（locked 显式标记/默认 forbid 均无有效期逻辑）——
    与事件级（≥3 次提及降级）不同，画像行不存在降级路径。
    """
    if not text or not text.strip():
        return False
    rows = db.execute(
        select(ProfileSensitive).where(ProfileSensitive.user_id == user_id)
    ).scalars().all()
    return _profile_hit(text, rows)


def _profile_hit(text: str, rows) -> bool:
    """命中画像敏感处置（forbid/caution/review）——rows 为已加载 ProfileSensitive 行

    S6-7：独立出纯函数，供 get_today_echo 一次加载复用（避免逐候选 N 次查询）。
    """
    for row in rows:
        topic = (row.topic or "").strip()
        if not topic:
            continue
        if topic in text and row.disposition in PROFILE_BLOCK_DISPOSITIONS:
            return True
    return False


def upsert_profile_sensitive(
    db: Session,
    user_id: str,
    topic: str,
    disposition: str = "forbid",
    evidence: list | None = None,
    locked: bool = False,
) -> ProfileSensitive:
    """画像敏感增/改（B1-6 对话式）：(user_id, topic) 存在则更新，否则插入。

    locked=True 为用户显式标记（永不过期语义强化）；返回值即最新行。
    """
    if disposition not in PROFILE_DISPOSITIONS:
        raise ValueError(f"disposition 非法：{disposition}（可选 {sorted(PROFILE_DISPOSITIONS)}）")
    topic = topic.strip()
    if not topic:
        raise ValueError("topic 不能为空")
    row = db.execute(
        select(ProfileSensitive).where(
            ProfileSensitive.user_id == user_id, ProfileSensitive.topic == topic
        )
    ).scalar_one_or_none()
    if row is None:
        row = ProfileSensitive(user_id=user_id, topic=topic)
        db.add(row)
    row.disposition = disposition
    row.evidence = list(evidence) if evidence else []
    row.locked = locked
    db.commit()
    db.refresh(row)
    return row


def delete_profile_sensitive(db: Session, user_id: str, topic: str) -> bool:
    """画像敏感删（B1-6）：删除该用户该话题；不存在返回 False"""
    row = db.execute(
        select(ProfileSensitive).where(
            ProfileSensitive.user_id == user_id, ProfileSensitive.topic == topic
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def list_profile_sensitive(
    db: Session, user_id: str, disposition: str | None = None
) -> list[ProfileSensitive]:
    """画像敏感查（B1-6）：列出该用户全部话题（可按处置级别过滤），按更新时间倒序"""
    q = select(ProfileSensitive).where(ProfileSensitive.user_id == user_id)
    if disposition:
        if disposition not in PROFILE_DISPOSITIONS:
            raise ValueError(f"disposition 非法：{disposition}")
        q = q.where(ProfileSensitive.disposition == disposition)
    q = q.order_by(ProfileSensitive.updated_at.desc())
    return db.execute(q).scalars().all()


def _is_sensitive(db: Session, content: Content, profile_rows=None) -> bool:
    """敏感双查（SAF-006/007；用户 2026-08-20 拍板：已有敏感标记 + LLM 检测）

    第零查（B5b FIX-4 前置画像 L1 校验）：profile_sensitive 命中 forbid/caution/review
    → 跳过不重提（画像级永不过期，无降级路径）
    第一查：内容入库时的敏感标记（contents.sensitive_status ≠ 正常）
    第二查：出包前 LLM 检测（llm_ops.base.moderate：规则预检 + 百炼护栏，fail-safe）

    profile_rows：可选已加载 ProfileSensitive 行（S6-7 get_today_echo 一次加载复用）。
    """
    text = content.text or ""
    if profile_rows is not None:
        if _profile_hit(text, profile_rows):
            return True
    elif profile_sensitive_blocked(db, str(content.user_id), text):
        return True
    if content.sensitive_status and content.sensitive_status != "正常":
        return True
    # 第二查：LLM 检测（对内容文本；mock 模式规则预检仍生效，fail-safe 拒发）
    if text.strip():
        from app.services.llm_ops.base import moderate

        verdict = moderate(text)
        if not verdict.get("pass", True):
            return True
    return False


def get_today_echo(db: Session, user_id: str) -> dict | None:
    """去年今日回响（每天 ≤1 条；无内容/已展示/敏感 → None）"""
    now_local = _local_now()
    today = now_local.date()
    # 修复（审查）：闰年 2/29 → 去年无 2/29 时 replace 抛 ValueError，退化到 2/28
    try:
        last_year = today.replace(year=today.year - 1)
    except ValueError:
        last_year = today.replace(year=today.year - 1, day=28)

    # 当天已展示条数（上限 1；日界按本地）
    day_start = datetime.combine(today, time.min, tzinfo=now_local.tzinfo)
    shown_today = db.scalar(
        select(func.count())
        .select_from(EchoHistory)
        .where(
            EchoHistory.user_id == user_id,
            EchoHistory.shown_at >= day_start,
            EchoHistory.action != "dismiss",
        )
    ) or 0
    if shown_today >= ECHO_DAILY_LIMIT:
        return None

    # 去年今日的内容（本地日期范围查询，走 taken_at 索引；审查 P1-11：
    # 原 func.extract(month/day) 不可走索引 + 逐条 N+1）
    day_start_local = datetime.combine(last_year, time.min, tzinfo=now_local.tzinfo)
    day_end_local = day_start_local + timedelta(days=1)
    row = db.execute(
        select(Content)
        .where(
            Content.user_id == user_id,
            Content.deleted_at.is_(None),
            Content.taken_at >= day_start_local,
            Content.taken_at < day_end_local,
        )
        .order_by(Content.taken_at.desc())
        .limit(20)
    ).scalars().all()

    # 已划掉指纹集合（一次 IN 查询，消除逐条 N+1）
    dismissed = set(
        db.execute(
            select(EchoHistory.fingerprint).where(
                EchoHistory.user_id == user_id,
                EchoHistory.action == "dismiss",
            )
        ).scalars().all()
    )

    # S6-7 敏感话题一次加载复用（逐候选 N 次查询 → 每调用 1 次）
    profile_rows = db.execute(
        select(ProfileSensitive).where(ProfileSensitive.user_id == user_id)
    ).scalars().all()

    llm_checked = False
    for content in row:
        text = content.text or ""
        # 便宜检查（画像敏感 + 入库敏感标记 + 已划掉）——不触发 LLM
        if _profile_hit(text, profile_rows):
            continue
        if content.sensitive_status and content.sensitive_status != "正常":
            continue
        fp = _fingerprint(content.id)
        if fp in dismissed:
            continue
        # LLM 检测仅首候选（S6-7）：出包前二次护栏只跑首个通过便宜检查的候选，
        # 避免对整批候选逐条 LLM（20 次 → ≤1 次）；首候选 LLM 未过 → 不再回退未验证候选
        if not llm_checked:
            llm_checked = True
            if text.strip():
                from app.services.llm_ops.base import moderate

                verdict = moderate(text)
                if not verdict.get("pass", True):
                    continue
        # 记录展示（回响每天≤1 条；event_id 为 NULL 避开 events 外键——事件服务 M2 接入）
        # 修复（审查 MAJOR 竞态）：查询计数无 DB 保护，并发双请求可同天两条——
        # 唯一索引 uq_echo_history_daily 兜底（shown_date 显式落列），插入冲突 →
        # 回滚按"已展示"处理。
        try:
            db.add(
                EchoHistory(
                    user_id=user_id,
                    event_id=None,
                    action="respond",
                    fingerprint=fp,
                    shown_date=today,
                )
            )
            db.commit()
        except IntegrityError:
            db.rollback()
            return None
        return {
            "content_id": content.id,
            "content_type": content.content_type,
            "text": text,
            "taken_at": content.taken_at.isoformat() if content.taken_at else None,
            "place": content.place,
            "echo_date": today.isoformat(),
            "fingerprint": fp,
        }
    return None


def dismiss_echo(db: Session, user_id: str, content_id: str) -> None:
    """划掉：不再出现（B5-a 回响交互；按 fingerprint 幂等）"""
    db.add(
        EchoHistory(
            user_id=user_id,
            event_id=None,
            action="dismiss",
            fingerprint=_fingerprint(content_id),
            shown_date=_local_now().date(),
        )
    )
    db.commit()
