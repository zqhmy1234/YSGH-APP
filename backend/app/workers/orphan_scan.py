"""孤儿对象扫描 job（B1 · Wave1 · 收尾 §5.2.6 = cleanup_job.py 头注 TODO(P0-6) 缺口补口）

背景：写后提交失败（best_effort_delete 也失败）/上传中止/审核拦截会产生无
contents 引用的存储对象（uploads/staging 分片、未注册 cos_key、wechat 敏感
拦截对象）。软删清理 job（cleanup_job.run_cleanup）只处理"有墓碑"的对象，管不到
这些"从未注册"的对象。本模块按"超龄 + 零引用"扫描并清理。

三类孤儿：
  ① uploads/staging 分片：uploads/{upload_id}/{index}.part —— upload_tasks 无
     对应"完成"记录（任务缺失且对象超龄；或任务存在但非 completed 且任务
     created_at 超龄 = 已放弃的上传；已完成任务残留分片按对象 mtime 判龄）
  ② 未注册 cos_key：photos/voice/thumbnails/ 下对象无 contents.cos_key /
     thumbnail_key 引用（写后提交失败且 best_effort_delete 也失败）
  ③ wechat 敏感拦截对象：wechat/ 下对象无 contents 引用（图片 CI 审核命中，
     services/wechat/service._process_media 返回 blocked 不建 Content，
     best_effort_delete 失败则残留）

引用判定：contents.cos_key + thumbnail_key 全表集合（含软删行——软删内容的对象
由 cleanup_job 在到期后物理清理，期间仍算被引用，避免本扫描抢跑破坏 30 天清理契约）。
超龄判定：对象 last_modified ≥ older_than_days；无 mtime 且无任务可判龄 → 保守跳过。
失败安全：单对象删除失败记日志继续，不中断整批；幂等可重跑；dry_run 只报告不删。

依赖契约（docs/parallel-dev-收尾/18 号 C7，由 B3 实现）：
  storage 后端新增 `list_objects(prefix)` —— 返回可迭代对象，每项归一为
  (key, last_modified)（容忍 tuple / 带 .key/.object_name + .last_modified 属性的对象）。
  B1 只消费不实现：B3 未合入前后端无 list_objects，本扫描返回 skipped 报告
  （fail-safe 不崩，调度侧日志可观测）。
  TODO(契约依赖·B3)：storage.py 实现 list_objects 后本模块零改动即可消费；
  merge 后由 B1 验证 backend/tests/test_orphan_scan.py 全绿。

调度（登记给集成 Agent）：与 cleanup_job 同调度——RQ 无内置 cron，需部署侧
挂定时（rq-scheduler / APScheduler / 系统 cron / Windows 计划任务），建议每天
低峰一次，30 天清理与孤儿扫描先后各跑一轮：
  python -m app.workers.cleanup_job --orphan-scan --older-than-days 30 --limit 500 --dry-run  # 观测
  python -m app.workers.cleanup_job --orphan-scan --older-than-days 30 --limit 500           # 执行
  （RQ worker 内亦可直接入队 run_orphan_scan 函数）
"""
from __future__ import annotations

import argparse
import logging
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Content, UploadTask
from app.db.session import SessionLocal
from app.services.external.storage import get_storage_backend

logger = logging.getLogger("yishu.orphan_scan")

# 已知对象前缀（各写入点）：photos/voice/thumbnails = 内容对象（含缩略图），
# wechat/ = 微信媒体，uploads/ = 分片 staging。域外前缀不扫描（未来新增前缀需登记）。
SCAN_PREFIXES = ("photos/", "voice/", "thumbnails/", "wechat/", "uploads/")
STAGING_PREFIX = "uploads/"

# by_category 固定键（与分类函数一一对应）
CATEGORY_KEYS = ("staging", "unregistered", "wechat")


def _classify(key: str) -> str | None:
    """按前缀归类：staging（分片）/ unregistered（未注册内容对象）/ wechat；域外返回 None"""
    if key.startswith(STAGING_PREFIX):
        return "staging"
    if key.startswith("wechat/"):
        return "wechat"
    if key.startswith(("photos/", "voice/", "thumbnails/")):
        return "unregistered"
    return None


def _parse_last_modified(value: Any) -> datetime | None:
    """归一 last_modified（datetime / ISO 字符串），无法解析返回 None"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (ValueError, TypeError):
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _iter_objects(listing: Iterable[Any]) -> Iterable[tuple[str, datetime | None]]:
    """把 B3 list_objects 返回的各种形状归一为 (key, last_modified)

    契约容忍：list[tuple[key, last_modified]]（规范形状）；也接受带
    .key/.object_name + .last_modified 属性的对象（SDK 返回的 dataclass / Object）。
    """
    for entry in listing:
        if isinstance(entry, tuple):
            key = entry[0] if len(entry) > 0 else None
            lm = _parse_last_modified(entry[1] if len(entry) > 1 else None)
        else:
            key = getattr(entry, "key", None) or getattr(entry, "object_name", None)
            lm = _parse_last_modified(getattr(entry, "last_modified", None))
        if not key:
            continue
        yield str(key), lm


def _load_referenced_keys(db: Session) -> set[str]:
    """contents 引用集合：cos_key + thumbnail_key 全表（含软删行，见模块 docstring）"""
    rows = db.execute(select(Content.cos_key, Content.thumbnail_key)).all()
    return {key for row in rows for key in (row[0], row[1]) if key}


def _load_staging_tasks(db: Session, upload_ids: set[str]) -> dict[str, UploadTask]:
    """按 upload_id 查 upload_tasks（staging 判据：任务是否存在/是否完成/是否超龄）"""
    if not upload_ids:
        return {}
    tasks = db.scalars(select(UploadTask).where(UploadTask.id.in_(upload_ids))).all()
    return {str(task.id): task for task in tasks}


def _is_orphan(
    key: str,
    lm: datetime | None,
    category: str,
    tasks: dict[str, UploadTask],
    referenced: set[str],
    cutoff: datetime,
) -> bool:
    """单对象孤儿判定：超龄 + 零引用（staging 按任务状态/龄，其余按引用集 + 对象龄）"""
    if category == "staging":
        upload_id = key.split("/")[1] if len(key.split("/")) > 1 else ""
        task = tasks.get(upload_id)
        if task is not None and task.status != "completed":
            # 未完成任务：任务 created_at 超龄 = 已放弃的上传（活跃上传年轻 → 保留可续传）
            return task.created_at is not None and task.created_at <= cutoff
        # 任务缺失 或 已完成（残留分片）：以对象 mtime 判龄（无 mtime 保守跳过）
        return lm is not None and lm <= cutoff
    if key in referenced:
        return False
    # 零引用 + 对象超龄（无 mtime 无法判龄 → 保守跳过，防误删新对象）
    return lm is not None and lm <= cutoff


def run_orphan_scan(
    older_than_days: int = 30,
    limit: int = 500,
    dry_run: bool = False,
    backend: Any = None,
    db: Session | None = None,
) -> dict:
    """执行一轮孤儿对象扫描，返回统计

    返回（对齐任务书 {scanned_orphans, deleted, failed} + 观测字段）：
      scanned_objects: list_objects 遍历到的已知前缀对象数
      scanned_orphans: 判定为孤儿（超龄 + 零引用）的候选数（不受 limit 截断）
      deleted:         实际删除数（dry_run 恒 0；≤ limit）
      failed:          单对象删除失败数（保留待下轮重试，不中断整批）
      by_category:     {"staging": n, "unregistered": n, "wechat": n}
      skipped:         True = 后端无 list_objects（B3 未合入），本轮跳过
      dry_run:         是否只报告不删

    backend 缺省走 get_storage_backend()；测试注入带 list_objects 的假后端。
    """
    backend = backend or get_storage_backend()
    list_objects = getattr(backend, "list_objects", None)
    if list_objects is None:
        logger.warning("存储后端无 list_objects（契约 18/C7 由 B3 实现）——本轮跳过孤儿扫描")
        return {
            "scanned_objects": 0,
            "scanned_orphans": 0,
            "deleted": 0,
            "failed": 0,
            "by_category": {k: 0 for k in CATEGORY_KEYS},
            "skipped": True,
            "dry_run": dry_run,
        }

    own_db = db is None
    db = db or SessionLocal()
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=older_than_days)
    try:
        referenced = _load_referenced_keys(db)
        listed: list[tuple[str, datetime | None]] = []
        for prefix in SCAN_PREFIXES:
            listed.extend(_iter_objects(list_objects(prefix)))
        staging_ids = {
            key.split("/")[1]
            for key, _ in listed
            if key.startswith(STAGING_PREFIX) and len(key.split("/")) > 1
        }
        tasks = _load_staging_tasks(db, staging_ids)

        orphans: list[tuple[str, str]] = []
        for key, lm in listed:
            category = _classify(key)
            if category is None:
                continue
            if _is_orphan(key, lm, category, tasks, referenced, cutoff):
                orphans.append((key, category))
        orphans.sort(key=lambda item: item[0])  # 确定性顺序（测试可断言）

        by_category = {c: 0 for c in CATEGORY_KEYS}
        for _, category in orphans:
            by_category[category] += 1

        deleted = failed = 0
        for key, category in orphans:
            if deleted + failed >= limit:
                break
            if dry_run:
                continue
            try:
                backend.delete_object(key)
                deleted += 1
                logger.info("孤儿对象已删 key=%s category=%s", key, category)
            except Exception as exc:  # noqa: BLE001 —— 单对象失败记日志继续
                failed += 1
                logger.warning("孤儿对象删除失败 key=%s category=%s: %s", key, category, exc)

        return {
            "scanned_objects": len(listed),
            "scanned_orphans": len(orphans),
            "deleted": deleted,
            "failed": failed,
            "by_category": by_category,
            "skipped": False,
            "dry_run": dry_run,
        }
    finally:
        if own_db:
            db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="孤儿对象扫描 job（P0-6 · Wave1 B1；超龄 + 零引用清理）"
    )
    parser.add_argument("--older-than-days", type=int, default=30)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true", help="只扫描不删")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    report = run_orphan_scan(
        older_than_days=args.older_than_days,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    logger.info("orphan-scan report: %s", report)
    print(report)


if __name__ == "__main__":
    main()
