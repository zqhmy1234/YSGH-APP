"""胶囊到期扫描（BA2 批 · 远期总账 A5）

扫描 status='sealed' 且 open_at<=now 的胶囊 → 置 'due' 中间态 → 经
notify.create_message 产「胶囊已到期」站内消息（msg_type=capsule_due，
payload/content_id 列同时带 content_id，客户端跳详情直用）。

幂等：同一胶囊只产一次消息——由 status 流转保证（sealed→due 是单次
状态迁移，重复扫描 WHERE status='sealed' 不再命中已置 due 的行）。

【触发机制选型】项目无进程内定时器（grep scheduler/cron 仅命中 notify.py
的部署侧 cron 备注；RQ worker 只做队列消费）。因此采用惰性触发 + 手动入口：
  - 惰性触发：GET /api/v1/capsules 时顺带跑一次扫描，模块级 monotonic
    节流（默认 30s）防高频列表请求抖动；
  - 手动入口：POST /api/v1/capsules/scan（登录态）直调 scan_due_capsules，
    不走节流，便于测试与运维补扫。
  未挂 GET /messages：messages.py 属并行批文件域，不可越界改；后续批次
  若引入 APScheduler/部署侧 cron，可把 scan_due_capsules 直接登记。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Capsule
from app.services.notify import create_message

logger = logging.getLogger("yishu.capsule_scan")

# 惰性触发节流（秒）：防列表高频请求反复打全表扫描
SCAN_THROTTLE_SECONDS = 30
_last_scan_ts: dict[str, float] = {"ts": 0.0}


def scan_due_capsules(db: Session, now: datetime | None = None) -> int:
    """扫描到期胶囊：sealed→due + 产到期消息（一次 commit 落库，幂等可重入）

    R2#2 事务边界：本函数是到期推送独立流程的最外层编排者（对齐
    generate_daily_review 口径）——create_message 只 flush，由这里统一 commit。
    返回本轮产出的到期消息数。
    """
    current = now or datetime.now(timezone.utc)
    rows = (
        db.execute(
            select(Capsule).where(Capsule.status == "sealed", Capsule.open_at <= current)
        )
        .scalars()
        .all()
    )
    for cap in rows:
        # 单次状态迁移 sealed→due 即消息幂等键：重复扫描不再命中本行
        cap.status = "due"
        create_message(
            db,
            cap.user_id,
            channel="in_app",
            msg_type="capsule_due",
            title="时间胶囊到啦",
            body="你封存的记忆到时间了，打开胶囊看看那时的自己吧。",
            payload={
                "content_id": str(cap.content_id),
                "capsule_id": str(cap.id),
                "template": "mock",
            },
        )
    if rows:
        db.commit()
        logger.info("胶囊到期扫描：产出 %d 条到期提醒", len(rows))
    return len(rows)


def maybe_scan_due(db: Session) -> int:
    """惰性触发入口（GET /capsules 顺带执行）：节流窗口内直接跳过"""
    now_mono = time.monotonic()
    if now_mono - _last_scan_ts["ts"] < SCAN_THROTTLE_SECONDS:
        return 0
    _last_scan_ts["ts"] = now_mono
    return scan_due_capsules(db)
