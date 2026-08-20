"""回响服务（P2-ECHO · 去年今日）

规则（产品部已确认）：
- 回响 = 去年今日的内容（照片/文字/语音），in-app 展示（非 push）
- 每天 ≤1 条（echo_history.shown_at 同日去重）；划掉（dismiss）不再出现
- 敏感排除（SAF-006/007 双查）：contents.sensitive_status ≠ 正常 跳过 +
  画像级敏感（profile_sensitive）命中跳过
- 指纹：内容 id 稳定派生（跨端同一条）
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Content, EchoHistory

logger = logging.getLogger("yishu.echo")

ECHO_DAILY_LIMIT = 1


def _fingerprint(content_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"echo:{content_id}"))


def _local_now() -> datetime:
    """本地时间（日界按本地口径：修复审查 MINOR——原按 UTC 日界，本地 0:00-8:00
    会被算到前一天）"""
    return datetime.now().astimezone()


def _is_sensitive(content: Content) -> bool:
    """敏感双查（SAF-006/007；用户 2026-08-20 拍板：已有敏感标记 + LLM 检测）

    第一查：内容入库时的敏感标记（contents.sensitive_status ≠ 正常）
    第二查：出包前 LLM 检测（dashscope.moderate：规则预检 + 百炼护栏，fail-safe）
    画像级敏感维度（B5b §2 更前置的画像校验）待画像敏感字段落地后接入（见 refactor-plan P1-06 后续）。
    """
    if content.sensitive_status and content.sensitive_status != "正常":
        return True
    # 第二查：LLM 检测（对内容文本；mock 模式规则预检仍生效，fail-safe 拒发）
    text = content.text or ""
    if text.strip():
        from app.services.external.dashscope import moderate

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

    for content in row:
        if _is_sensitive(content):
            continue
        fp = _fingerprint(content.id)
        if fp in dismissed:
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
            "text": content.text,
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
