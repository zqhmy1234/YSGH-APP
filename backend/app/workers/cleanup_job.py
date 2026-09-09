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

孤儿对象扫描（P0-6 落地 · 2026-09-10 重构审计波 B）：run_cleanup 每轮附带
run_upload_reaper——① upload_tasks 中 status 非 completed/failed 且 updated_at
超龄（默认 7 天）的僵死任务：删全部 staging 分片对象 + upload_chunks 行，
任务标 failed（保留行=client_upload_id 幂等键语义不破坏，客户端自然重新 init）；
② fs 后端专属：uploads/ 目录下无任务行对应的孤儿目录（mtime 超龄）整目录删除
（fake 单例测试环境跳过目录扫描；cos/minio 的孤儿对象扫描列入后续波，需 list_objects）。
写后提交失败（best_effort_delete 也失败）/审核拦截产生的未注册 cos_key 对象
扫描仍为遗留（需 list 全量对比，量大时低峰跑）。

调度（登记给集成 Agent）：
  RQ 无内置 cron —— 需集成 Agent 在部署侧挂定时（rq-scheduler / APScheduler /
  系统 cron / Windows 计划任务），建议每天低峰一次：
      python -m app.workers.cleanup_job --older-than-days 30 --limit 500
  （RQ worker 内亦可直接入队 run_cleanup 函数）
"""
from __future__ import annotations

import argparse
import logging
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.db.models import Content, DeletedLog, SyncFieldVersion, UploadTask
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


def _reap_stale_tasks(db: Session, cutoff: datetime, limit: int, dry_run: bool) -> dict:
    """① upload_tasks 僵死任务（status 非 completed/failed 且 updated_at 超龄）：
    删 staging 分片 + chunks 行，任务标 failed（行保留=幂等键不破坏）。
    单任务失败 rollback 继续（下次重试），不中断整批。"""
    from app.services.upload.protocol import discard_staging_for_task

    stale = db.scalars(
        select(UploadTask)
        .where(
            UploadTask.status.notin_(("completed", "failed")),
            UploadTask.updated_at <= cutoff,
        )
        .order_by(UploadTask.updated_at)
        .limit(limit)
    ).all()
    reaped = failed = chunks_removed = 0
    for task in stale:
        try:
            if not dry_run:
                backend = get_storage_backend(task.storage)
                n = discard_staging_for_task(db, task, backend)
                db.commit()
                chunks_removed += n
            reaped += 1
        except Exception as exc:  # noqa: BLE001 —— 单任务失败留待下轮
            db.rollback()
            failed += 1
            logger.warning("reaper 清任务失败 task=%s: %s", task.id, exc)
    return {"reaped": reaped, "failed": failed, "chunks_removed": chunks_removed}


def _reap_orphan_dirs(db: Session, older_than_days: int) -> dict:
    """② fs 后端专属：uploads/ 下无任务行对应的孤儿目录（内容 mtime 超龄）整目录删；
    另清「任务已 completed 但 staging 残留」的目录（complete 已删分片，目录壳无害但占 inode）。
    fake/minio/cos 无本地目录（local_root()=None）→ 跳过（cos/minio 孤儿需 list_objects，后续波）。"""
    root = get_storage_backend().local_root()
    if root is None:
        return {"orphan_dirs": 0, "leftover_dirs": 0, "skipped_backend": True}
    uploads = Path(root) / "uploads"
    if not uploads.is_dir():
        return {"orphan_dirs": 0, "leftover_dirs": 0, "skipped_backend": False}
    # 前缀查询走主键 IN 全量任务（任务数有界，296 级无压力；status 一并取）
    task_status = {str(r[0]): r[1] for r in db.execute(text("SELECT id, status FROM upload_tasks")).fetchall()}

    def _dir_age_days(d: Path) -> float:
        mt = d.stat().st_mtime
        for f in d.iterdir():
            try:
                mt = max(mt, f.stat().st_mtime)
            except OSError:
                pass
        return (datetime.now(timezone.utc) - datetime.fromtimestamp(mt, tz=timezone.utc)).total_seconds() / 86400

    orphan = leftover = 0
    for d in uploads.iterdir():
        if not d.is_dir():
            continue
        name = d.name
        st = task_status.get(name)
        try:
            if st is None:
                # 无任务行 → 孤儿；超龄才删（防误删 in-flight init 与写目录之间的竞态窗）
                if _dir_age_days(d) >= older_than_days:
                    shutil.rmtree(d)
                    orphan += 1
            elif st == "completed":
                # 任务完成但 staging 残留（complete 删片后目录壳/异常半路）→ 直接清
                shutil.rmtree(d)
                leftover += 1
        except OSError as exc:  # noqa: BLE001 —— 占用/权限失败下轮重试
            logger.warning("reaper 删目录失败 %s: %s", d, exc)
    return {"orphan_dirs": orphan, "leftover_dirs": leftover, "skipped_backend": False}


def run_upload_reaper(older_than_days: int = 7, limit: int = 200, dry_run: bool = False) -> dict:
    """孤儿上传分片 reaper（P0-6 遗留项落地 · 2026-09-10 审计波 B）。

    两轮扫描：①DB 僵死任务 ②fs 孤儿/残留目录。返回 {reaped, failed,
    chunks_removed, orphan_dirs, leftover_dirs, skipped_backend}。
    幂等可重跑；与 run_cleanup 同调度（每天低峰一次）。
    """
    db: Session = SessionLocal()
    try:
        cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
        report = _reap_stale_tasks(db, cutoff, limit, dry_run)
        if dry_run:
            report.update({"orphan_dirs": 0, "leftover_dirs": 0, "skipped_backend": False})
        else:
            report.update(_reap_orphan_dirs(db, older_than_days))
        logger.info("upload reaper: %s", report)
        return report
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="30 天软删除物理清理 job（B4 Wave3 AgentG）+ 上传分片 reaper（P0-6）")
    parser.add_argument("--older-than-days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true", help="只扫描不物理删")
    parser.add_argument("--reaper-older-than-days", type=int, default=7, help="上传僵死任务/孤儿目录阈值")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    report = run_cleanup(older_than_days=args.older_than_days, limit=args.limit, dry_run=args.dry_run)
    logger.info("cleanup report: %s", report)
    print(report)
    reap = run_upload_reaper(older_than_days=args.reaper_older_than_days, dry_run=args.dry_run)
    print(reap)


if __name__ == "__main__":
    main()
