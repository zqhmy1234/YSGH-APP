"""超龄 failed/processing 内容重扫 job（A2 · P0-2 RQ 重试语义修正配套）

背景（P0-2/P0-4）：RQ Retry(3) 只在任务抛异常时触发；历史上 process_content 对
AsrError/Exception 全部吞掉（写 status=failed 后正常返回）→ 重试永不点火。
A2 修复后 retryable 错误会 re-raise（RQ 3 次指数退避重投），但仍有残留需本 job 兜底：
  1. 修复上线前已产生的 failed/processing 历史卡死记录（P0-4 遗留）
  2. RQ 3 次重投仍失败（网络长抖）后滞留 failed 的记录
  3. worker 崩溃/被杀/job_timeout 杀任务遗留的 processing 卡死记录

对 updated_at 超龄（默认 >1h）的 failed/processing 内容逐条处置：
  - failed 且 extra.error.retryable=True → 重新入队 process_content（重扫计数 +1）
  - failed 且 retryable=False/无标记 → 已终态，跳过（不重投）
  - processing 超龄 → 重新入队（worker 崩溃遗留；重扫计数 +1）
  - 重扫计数 ≥ REQUEUE_MAX_ATTEMPTS → 置终态：status=failed +
    extra.error（retryable=False + requeue_exhausted=True，保留原错误码溯源），不再骚扰

失败安全：单行失败记日志不中断整批；幂等（重投/置终态都会前移 updated_at，
同轮不重复选中，下一轮按新超龄阈值重新评估）。
重投顺序：先 enqueue 后落计数——Redis 故障时 enqueue 抛错 → 计数不动下轮重试，
避免计数虚增导致永久故障内容被误置终态。

注意：RQ 无内置 cron——调度登记给集成 Agent（与 cleanup_job 同机制，建议每
1h 低峰跑一次，limit 默认 100）：
    python -m app.workers.requeue_job [--older-than-seconds 3600] [--limit 100] [--dry-run]
"""
from __future__ import annotations

import argparse
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.queue import enqueue_high
from app.db.models import Content
from app.db.session import SessionLocal
from app.services.pipeline import process_content

logger = logging.getLogger("yishu.requeue")

# 超龄阈值：updated_at 超过该秒数视为卡死/滞留。ASR job_timeout=600s << 3600s，
# 正常在跑任务不会被误选；RQ 3 次退避重投总窗口（10+30+90s）也远小于该值。
REQUEUE_DEFAULT_STALE_SECONDS = 3600
# 单条内容重扫次数上限（防永久故障内容无限重投骚扰下游/计费）
REQUEUE_MAX_ATTEMPTS = 3


def _stale_candidates(db: Session, cutoff: datetime, limit: int) -> list[Content]:
    """选中超龄 failed/processing 内容（updated_at 升序，最旧优先）"""
    return list(
        db.scalars(
            select(Content)
            .where(
                Content.status.in_(("failed", "processing")),
                Content.updated_at <= cutoff,
                Content.deleted_at.is_(None),
            )
            .order_by(Content.updated_at)
            .limit(limit)
        ).all()
    )


def _requeue_count(content: Content) -> int:
    """重扫计数（存 extra.requeue_count，job 级簿记，与 error 明细解耦）"""
    return int((content.extra or {}).get("requeue_count") or 0)


def _bump_requeue_count(content: Content) -> int:
    """重扫计数 +1（updated_at 随 commit 由 ORM onupdate 前移，防同轮重复选中）"""
    extra = dict(content.extra or {})
    n = int(extra.get("requeue_count") or 0) + 1
    extra["requeue_count"] = n
    content.extra = extra
    return n


def _finalize_exhausted(content: Content) -> None:
    """重扫超限 → 置终态：status=failed + error.retryable=False（保留原错误码溯源）"""
    extra = dict(content.extra or {})
    error = dict(extra.get("error") or {})
    error.update(
        {
            "outcome": "failed_final",
            "retryable": False,
            "requeue_exhausted": True,
        }
    )
    if "code" not in error:
        error["code"] = "REQUEUE_EXHAUSTED"
    if "message" not in error:
        error["message"] = f"重扫 {REQUEUE_MAX_ATTEMPTS} 次仍未成功，置终态不再自动重试"
    extra["error"] = error
    if content.content_type == "voice":
        # 保持 voice 审计形状（audio_processing.outcome）
        audio = dict(extra.get("audio_processing") or {})
        audio.update(
            {
                "outcome": "failed_final",
                "retryable": False,
                "requeue_exhausted": True,
            }
        )
        extra["audio_processing"] = audio
    content.extra = extra
    content.status = "failed"


def run_requeue(
    older_than_seconds: int = REQUEUE_DEFAULT_STALE_SECONDS,
    limit: int = 100,
    dry_run: bool = False,
) -> dict:
    """执行一轮失败/卡死重扫，返回 {scanned, requeued, finalized, skipped_final, failed}

    - scanned: 本轮选中（超龄 failed/processing）数
    - requeued: 重新入队 process_content 数（重扫计数 +1）
    - finalized: 重扫超限置终态数
    - skipped_final: 终态失败（retryable=False）跳过数
    - failed: 单行处置异常数（保留原状，下轮重试）
    """
    db: Session = SessionLocal()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=older_than_seconds)
    requeued = finalized = skipped_final = failed = 0
    try:
        candidates = _stale_candidates(db, cutoff, limit)
        for content in candidates:
            try:
                error = (content.extra or {}).get("error") or {}
                retryable = bool(error.get("retryable"))
                if _requeue_count(content) >= REQUEUE_MAX_ATTEMPTS:
                    # 重扫超限：置终态（failed + 不可重试），不再骚扰
                    if not dry_run:
                        _finalize_exhausted(content)
                        db.commit()
                    finalized += 1
                    continue
                if content.status == "failed" and not retryable:
                    # 终态失败（AUDIO_NOT_FOUND/敏感拦截等）：跳过，不重投
                    skipped_final += 1
                    continue
                # failed+retryable 或 processing 卡死 → 重新入队
                if not dry_run:
                    enqueue_high(process_content, str(content.id))
                    _bump_requeue_count(content)
                    db.commit()
                requeued += 1
            except Exception as exc:  # noqa: BLE001 —— 单行失败不中断整批
                db.rollback()
                failed += 1
                logger.warning("重扫处置失败 content=%s: %s", content.id, exc)
        return {
            "scanned": len(candidates),
            "requeued": requeued,
            "finalized": finalized,
            "skipped_final": skipped_final,
            "failed": failed,
        }
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="超龄 failed/processing 内容重扫 job（A2 · P0-2）"
    )
    parser.add_argument(
        "--older-than-seconds",
        type=int,
        default=REQUEUE_DEFAULT_STALE_SECONDS,
        help="updated_at 超龄阈值（秒），默认 3600",
    )
    parser.add_argument("--limit", type=int, default=100, help="每轮最多处理条数")
    parser.add_argument("--dry-run", action="store_true", help="只扫描统计，不重投/不置终态")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    report = run_requeue(
        older_than_seconds=args.older_than_seconds,
        limit=args.limit,
        dry_run=args.dry_run,
    )
    logger.info("requeue report: %s", report)
    print(report)


if __name__ == "__main__":
    main()
