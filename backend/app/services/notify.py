"""消息通知服务（S4-07 推送 + S4-08 消息中心）

- create_message：统一消息入库（in-app 与 push 同表 messages）
- 推送通道：推送厂商凭证未配置 → mock 通道（日志占位 MOCK_PUSH）；
  配置后零切换（channel == push 时走真实厂商，见 TODO(T1)）
- generate_daily_review：22:00 每日复盘（产品部推送策略：复盘走 push）；
  无内容用户跳过（防打扰）
- notify_voice_done：语音处理完成 push（S4-07 第二类 push）

关怀追问（care_followup）in-app 消息：骨架池/文案库待产品部提供，
msg_type 已预留，生成逻辑接入时复用 create_message。
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Content, Message

logger = logging.getLogger("yishu.notify")

# 复盘生成时区（产品口径：本地 22:00；MVP 中国区固定 +08:00）
REVIEW_TZ = timezone(timedelta(hours=8))

# 内容类型中文（复盘文案用）
_TYPE_CN = {
    "photo": "照片",
    "text": "文字",
    "voice": "语音",
    "article": "文章",
}


def create_message(
    db: Session,
    user_id: str,
    channel: str,
    msg_type: str,
    title: str,
    body: str,
    payload: dict | None = None,
) -> Message:
    """统一消息入库（in-app 与 push 同表）；push 消息经 mock 通道发送"""
    msg = Message(
        user_id=user_id,
        channel=channel,
        msg_type=msg_type,
        title=title,
        body=body,
        payload=payload or {},
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)

    if channel == "push":
        # 推送厂商凭证未配置 → mock 通道（S4-07：交付调度+消息生成+消息中心；
        # 凭证到位后在此接入真实厂商，幂等键 = messages.id）
        logger.info("[MOCK_PUSH] user=%s msg_id=%s title=%s body=%s", user_id, msg.id, title, body)
    return msg


def _day_range(day: date) -> tuple[datetime, datetime]:
    """本地日界 [day 00:00, day+1 00:00)（复盘按本地日界统计）"""
    start = datetime.combine(day, time.min, tzinfo=REVIEW_TZ)
    return start, start + timedelta(days=1)


def _today_stats(db: Session, user_id: str, day: date) -> dict[str, int]:
    """今日内容统计（按 content_type；只计非软删、非敏感）"""
    start, end = _day_range(day)
    rows = db.execute(
        select(Content.content_type, func.count())
        .where(
            Content.user_id == user_id,
            Content.deleted_at.is_(None),
            or_(Content.sensitive_status.is_(None), Content.sensitive_status == "正常"),
            Content.taken_at >= start,
            Content.taken_at < end,
        )
        .group_by(Content.content_type)
    ).all()
    return {t: n for t, n in rows}


def generate_daily_review(db: Session, user_id: str, day: date | None = None) -> Message | None:
    """每日复盘（22:00 push）：汇总今日内容；无内容返回 None（防打扰）"""
    day = day or datetime.now(REVIEW_TZ).date()
    stats = _today_stats(db, user_id, day)
    if not stats:
        logger.info("user=%s 今日无内容，跳过复盘", user_id)
        return None

    total = sum(stats.values())
    parts = "、".join(f"{_TYPE_CN.get(t, t)} {n} 条" for t, n in sorted(stats.items()))
    return create_message(
        db,
        user_id,
        channel="push",
        msg_type="daily_review",
        title=f"{day.month}月{day.day}日 · 今日回顾",
        body=f"今天记下了 {total} 条记忆（{parts}）。睡前花一分钟看看，让日子被记住。",
        payload={"day": day.isoformat(), "stats": stats, "template": "mock"},
    )


def notify_voice_done(db: Session, user_id: str, content_id: str) -> Message:
    """语音处理完成 push（S4-07：语音异步转写完成后通知）"""
    return create_message(
        db,
        user_id,
        channel="push",
        msg_type="voice_done",
        title="语音已整理好",
        body="你刚刚的语音已经整理完成，可以来看看。",
        payload={"content_id": content_id, "template": "mock"},
    )
