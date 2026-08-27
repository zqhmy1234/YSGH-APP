"""孤儿对象扫描 job（B1 · Wave1 · 收尾 §5.2.6 = cleanup_job.py 头注 TODO(P0-6) 缺口补口）
任务卡：docs/parallel-dev-收尾/06（Wave1 Agent B1 深化版 v2）

背景：写后提交失败（best_effort_delete 也失败）/上传中止/审核拦截会产生无
contents 引用的存储对象（uploads/staging 分片、未注册 cos_key、wechat 敏感
拦截对象）。软删清理 job（cleanup_job.run_cleanup）只处理"有墓碑"的对象，管不到
这些"从未注册"的对象。本模块按"超龄 + 零引用"扫描并清理。

三类孤儿（任务卡 §1.1）：
  ① uploads/staging 分片：uploads/{upload_id}/{index}.part —— upload_tasks 无
     对应"完成"记录（任务缺失且对象超龄；或任务存在但非 completed 且任务
     created_at 超龄 = 已放弃的上传；已完成任务残留分片按对象 mtime 判龄）
     超龄阈值 ORPHAN_STAGING_MAX_AGE_DAYS = 1（当天未完成即清）
  ② 未注册 cos_key：photos/voice/thumbnails/ 下对象无 contents.cos_key /
     thumbnail_key 引用（写后提交失败且 best_effort_delete 也失败）
     超龄阈值 ORPHAN_OBJECT_MAX_AGE_DAYS = 30（对齐 30 天物理清理口径）
  ③ wechat 敏感拦截对象：wechat/ 下对象无 contents 引用（图片 CI 审核命中，
     services/wechat/service._process_media 返回 blocked 不建 Content，
     best_effort_delete 失败则残留）；阈值同 ②

引用集（任务卡 §1.2，防误删铁律）：
  R1 = contents.cos_key + thumbnail_key 全表（含软删行——软删内容的对象由
       cleanup_job 在到期后物理清理，期间仍算被引用，避免本扫描抢跑破坏 30 天清理契约）
  R2 = upload_tasks 已完成记录的 file_key（complete 落对象后、注册 contents 前的
       空窗期保护——防误删刚完成上传、尚未建内容记录的最终对象）
  孤儿 = list_objects(prefix) 全量 key − (R1 ∪ R2)，再按类过滤超龄。

删除策略（任务卡 §1.3）：
  1. 默认 dry_run=False 由调用方决定；**调度首跑必须 dry-run**（只统计不删）。
  2. 删除用 best_effort_delete(key, backend)（storage.py 既有：失败记日志不中断），
     删后 object_exists 校验计数 failed（best_effort_delete 吞异常，靠校验补计数）。
  3. 幂等可重跑：已删对象 list 不到 → 自然跳过；单对象失败保留待下次。
  4. 安全护栏：limit 默认 500；阈值参数化 --orphan-staging-max-age / --orphan-object-max-age。

依赖契约（docs/parallel-dev-收尾/18 号 C7，由 B3 实现）：
  storage 后端新增 `list_objects(prefix)` —— 返回可迭代对象，每项归一为
  (key, last_modified)（容忍 tuple / 带 .key/.object_name + .last_modified 属性的对象）。
  B1 只消费不实现：B3 未合入前后端无 list_objects，本扫描返回 skipped 报告
  （fail-safe 不崩，调度侧日志可观测）。
  TODO(契约依赖·B3)：storage.py 实现 list_objects 后本模块零改动即可消费；
  merge 后由 B1 验证 backend/tests/test_orphan_scan.py 全绿。

调度（登记给集成 Agent，任务卡 §2.3）：与 cleanup_job 同调度——RQ 无内置 cron，
需部署侧挂定时（rq-scheduler / APScheduler / 系统 cron / Windows 计划任务），
每天低峰一次，**首跑必须 dry-run 观测**，确认无误再切执行：
  python -m app.workers.cleanup_job --orphan-scan --dry-run                          # 首跑观测
  python -m app.workers.cleanup_job --orphan-scan --orphan-object-max-age 30 --limit 500  # 执行
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
from app.services.external.storage import best_effort_delete, get_storage_backend

logger = logging.getLogger("yishu.orphan_scan")

# 已知对象前缀（各写入点）：photos/voice/thumbnails = 内容对象（含缩略图），
# wechat/ = 微信媒体，uploads/ = 分片 staging。域外前缀不扫描（未来新增前缀需登记）。
SCAN_PREFIXES = ("photos/", "voice/", "thumbnails/", "wechat/", "uploads/")
STAGING_PREFIX = "uploads/"

# 超龄阈值（任务卡 §1.1）：staging 当天未完成即清；未注册/wechat 对齐 30 天物理清理口径
ORPHAN_STAGING_MAX_AGE_DAYS = 1
ORPHAN_OBJECT_MAX_AGE_DAYS = 30

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
    """引用集 R1 ∪ R2（任务卡 §1.2）：
    R1 = contents.cos_key + thumbnail_key 全表（含软删行）；
    R2 = upload_tasks 已完成记录的 file_key（完成→注册空窗期保护）。
    """
    rows = db.execute(select(Content.cos_key, Content.thumbnail_key)).all()
    referenced = {key for row in rows for key in (row[0], row[1]) if key}
    completed_keys = db.scalars(
        select(UploadTask.file_key).where(UploadTask.status == "completed")
    ).all()
    referenced.update(k for k in completed_keys if k)
    return referenced


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
    staging_cutoff: datetime,
    object_cutoff: datetime,
) -> bool:
    """单对象孤儿判定：超龄 + 零引用（staging 按任务状态/龄 + 1 天阈值，其余按引用集 + 30 天阈值）"""
    if category == "staging":
        upload_id = key.split("/")[1] if len(key.split("/")) > 1 else ""
        task = tasks.get(upload_id)
        if task is not None and task.status != "completed":
            # 未完成任务：任务 created_at 超龄（>1 天） = 已放弃的上传（活跃上传年轻 → 保留可续传）
            return task.created_at is not None and task.created_at <= staging_cutoff
        # 任务缺失（init 未落库/任务被删）或已完成（残留分片）：以对象 mtime 判龄（无 mtime 保守跳过）
        return lm is not None and lm <= staging_cutoff
    if key in referenced:
        return False
    # 零引用 + 对象超龄（无 mtime 无法判龄 → 保守跳过，防误删新对象）
    return lm is not None and lm <= object_cutoff


def run_orphan_scan(
    staging_max_age_days: int = ORPHAN_STAGING_MAX_AGE_DAYS,
    object_max_age_days: int = ORPHAN_OBJECT_MAX_AGE_DAYS,
    limit: int = 500,
    dry_run: bool = False,
    backend: Any = None,
    db: Session | None = None,
) -> dict:
    """执行一轮孤儿对象扫描，返回统计（任务卡 §1.3.4 六字段 + 观测字段）

    scanned_keys:       list_objects 遍历到的已知前缀对象数
    orphans_total:      孤儿候选总数（超龄 + 零引用，不受 limit 截断）
    orphans_staging:    其中 uploads/staging 分片数
    orphans_unregistered: 其中未注册 cos_key（photos/voice/thumbnails）数
    orphans_wechat:     其中 wechat 敏感拦截对象数
    deleted:            实际删除数（dry_run 恒 0；≤ limit）
    failed:             删除后校验仍存在 / 校验异常数（保留待下轮重试，不中断整批）
    skipped:            True = 后端无 list_objects（B3 未合入），本轮跳过
    dry_run:            是否只报告不删

    backend 缺省走 get_storage_backend()；测试注入带 list_objects 的假后端。
    """
    backend = backend or get_storage_backend()
    list_objects = getattr(backend, "list_objects", None)
    if list_objects is None:
        logger.warning("存储后端无 list_objects（契约 18/C7 由 B3 实现）——本轮跳过孤儿扫描")
        return {
            "scanned_keys": 0,
            "orphans_total": 0,
            "orphans_staging": 0,
            "orphans_unregistered": 0,
            "orphans_wechat": 0,
            "deleted": 0,
            "failed": 0,
            "skipped": True,
            "dry_run": dry_run,
        }

    own_db = db is None
    db = db or SessionLocal()
    now = datetime.now(timezone.utc)
    staging_cutoff = now - timedelta(days=staging_max_age_days)
    object_cutoff = now - timedelta(days=object_max_age_days)
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
            if _is_orphan(key, lm, category, tasks, referenced, staging_cutoff, object_cutoff):
                orphans.append((key, category))
        orphans.sort(key=lambda item: item[0])  # 确定性顺序（测试可断言）

        counts = {c: 0 for c in CATEGORY_KEYS}
        for _, category in orphans:
            counts[category] += 1

        deleted = failed = 0
        for key, category in orphans:
            if deleted + failed >= limit:
                break
            if dry_run:
                continue
            try:
                # 任务卡 §1.3.2：删除走 best_effort_delete（失败记日志不中断）；
                # 其吞异常 → 删后 object_exists 校验，仍存在则计 failed。
                best_effort_delete(key, backend)
                if backend.object_exists(key):
                    failed += 1
                    logger.warning("孤儿对象删除后仍存在（疑似失败）key=%s category=%s", key, category)
                else:
                    deleted += 1
                    logger.info("孤儿对象已删 key=%s category=%s", key, category)
            except Exception as exc:  # noqa: BLE001 —— 校验异常兜底，不中断整批
                failed += 1
                logger.warning("孤儿对象删除校验异常 key=%s category=%s: %s", key, category, exc)

        return {
            "scanned_keys": len(listed),
            "orphans_total": len(orphans),
            "orphans_staging": counts["staging"],
            "orphans_unregistered": counts["unregistered"],
            "orphans_wechat": counts["wechat"],
            "deleted": deleted,
            "failed": failed,
            "skipped": False,
            "dry_run": dry_run,
        }
    finally:
        if own_db:
            db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="孤儿对象扫描 job（P0-6 · Wave1 B1；超龄 + 零引用清理，任务卡 §1.3.5）"
    )
    parser.add_argument(
        "--orphan-staging-max-age", type=int, default=ORPHAN_STAGING_MAX_AGE_DAYS,
        help=f"staging 分片超龄阈值（天，默认 {ORPHAN_STAGING_MAX_AGE_DAYS}：当天未完成即清）",
    )
    parser.add_argument(
        "--orphan-object-max-age", type=int, default=ORPHAN_OBJECT_MAX_AGE_DAYS,
        help=f"未注册/wechat 对象超龄阈值（天，默认 {ORPHAN_OBJECT_MAX_AGE_DAYS}：对齐物理清理）",
    )
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true", help="只扫描不删（调度首跑必须 dry-run）")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    report = run_orphan_scan(
        staging_max_age_days=args.orphan_staging_max_age,
        object_max_age_days=args.orphan_object_max_age,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    logger.info("orphan-scan report: %s", report)
    print(report)


if __name__ == "__main__":
    main()
