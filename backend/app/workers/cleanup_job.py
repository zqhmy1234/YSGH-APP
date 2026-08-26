"""30 天物理清理 job（B4 · Wave3 AgentG · audit #2 缺口修复）

背景：`deleted_logs.cleanup_status` 列默认 "pending"（sync.py:120 写入），全仓无任何
消费方——软删墓碑只增不清理，设计承诺"保留 30 天后物理清理"未落地。本 job 补齐消费方：

对 deleted_at ≥ 30 天 且 cleanup_status=pending 的墓碑：
  1. 物理删 COS 对象（contents.cos_key 原件 + thumbnail_key 缩略图）
  2. 物理删 contents 行
  3. 清墓碑：删 sync_field_versions 该 entity 全部行（entity 级 "*" 墓碑 + 字段行）
  4. 标记 deleted_logs.cleanup_status = "done"（保留审计轨迹；下次不再选中）

失败安全：单行失败保留 pending 并记日志（下次运行重试），不中断整批；
job 可重复运行（幂等：done 行跳过，对象已删则静默）。

调度（登记给集成 Agent）：
  RQ 无内置 cron —— 需集成 Agent 在部署侧挂定时（rq-scheduler / APScheduler /
  系统 cron / Windows 计划任务），建议每天低峰一次：
      python -m app.workers.cleanup_job --older-than-days 30 --limit 500
  （RQ worker 内亦可直接入队 run_cleanup 函数）
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Content, DeletedLog, SyncFieldVersion
from app.db.session import SessionLocal
from app.services.external.storage import get_storage_backend

logger = logging.getLogger("yishu.cleanup")


def _delete_content_objects(db: Session, content_id: str) -> None:
    """物理删 COS 对象（原件 + 缩略图；对象不存在静默）"""
    content = db.get(Content, content_id)
    if content is None:
        return
    backend = get_storage_backend()
    for key in (content.cos_key, content.thumbnail_key):
        if not key:
            continue
        try:
            backend.delete_object(key)
        except Exception as exc:  # noqa: BLE001 —— 对象删除失败记日志继续
            logger.warning("物理清理删对象失败 key=%s: %s", key, exc)
    # 物理删 contents 行
    db.delete(content)


def _clear_tombstone(db: Session, content_id: str) -> None:
    """清墓碑：删 sync_field_versions 该 entity 全部行（含 entity 级 "*" 墓碑）"""
    db.execute(
        SyncFieldVersion.__table__.delete().where(
            SyncFieldVersion.entity_id == content_id
        )
    )


def run_cleanup(older_than_days: int = 30, limit: int = 500, dry_run: bool = False) -> dict:
    """执行一轮物理清理，返回 {scanned, cleaned, failed, skipped_not_due}

    - scanned: 本轮选中（到期 pending）的墓碑数
    - cleaned: 成功物理清理数（cleanup_status → done）
    - failed: 失败数（保留 pending，下次重试）
    - skipped_not_due: 未到期 pending 数（仅用于调度观测）
    """
    db: Session = SessionLocal()
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    cleaned = failed = 0
    not_due = 0
    try:
        due = db.scalars(
            select(DeletedLog)
            .where(
                DeletedLog.cleanup_status == "pending",
                DeletedLog.deleted_at <= cutoff,
            )
            .order_by(DeletedLog.deleted_at)
            .limit(limit)
        ).all()
        if len(due) < limit:
            # 调度观测：统计到期外的 pending 总数（未超 limit 才准确）
            not_due = db.scalar(
                select(func.count())
                .select_from(DeletedLog)
                .where(
                    DeletedLog.cleanup_status == "pending",
                    DeletedLog.deleted_at > cutoff,
                )
            ) or 0

        for log in due:
            try:
                if not dry_run:
                    _delete_content_objects(db, str(log.content_id))
                    _clear_tombstone(db, str(log.content_id))
                    log.cleanup_status = "done"
                    db.commit()
                cleaned += 1
            except Exception as exc:  # noqa: BLE001 —— 单行失败保留 pending 重试
                db.rollback()
                failed += 1
                logger.warning("物理清理失败 content=%s: %s", log.content_id, exc)
        return {
            "scanned": len(due),
            "cleaned": cleaned,
            "failed": failed,
            "skipped_not_due": not_due,
        }
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="30 天软删除物理清理 job（B4 Wave3 AgentG）")
    parser.add_argument("--older-than-days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true", help="只扫描不物理删")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    report = run_cleanup(older_than_days=args.older_than_days, limit=args.limit, dry_run=args.dry_run)
    logger.info("cleanup report: %s", report)
    print(report)


if __name__ == "__main__":
    main()
