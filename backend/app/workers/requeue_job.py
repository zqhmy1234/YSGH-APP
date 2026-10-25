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

## 调度（2026-10-25 · D-23 修复：这里曾经是**空头承诺**）

原注释只有一句「RQ 无内置 cron——调度登记给集成 Agent（建议每 1h 跑一次）」，
而实际上**全仓没有任何 cron / systemd timer / RQ scheduler / 代码路径执行它**。
后果是 `process_content` 异常路径里承诺的「RQ 重投耗尽 → requeue_job 超龄重扫兜底」
**从来没发生过**：`processing` 积压 4520 条（含 failed 共 5157），最早 8/27、
最新 0.6 小时前，完全静默（`qdrant_text_id` 非空数 0/4520 —— 向量化从未执行）。

现在调度是**可执行的交付物**，不再只是注释：
  · `deploy/systemd/yishu-requeue.service`（Type=oneshot，flock 防跨轮并发）
  · `deploy/systemd/yishu-requeue.timer`（每 30 分钟 + Persistent=true + OnBootSec=5min）
  · 由 `deploy/scripts/deploy_one.sh` 第 3 步自动安装；安装/验证命令见
    `deploy/RUNBOOK.md` §5.1

手动执行（排查用）：
    python -m app.workers.requeue_job [--older-than-seconds 3600] [--limit 100] [--dry-run]

## 幂等说明（改调度前必读）

本 job **不走 `enqueue_unique`**，而是直接 `enqueue_high(process_content, id)` ——
**没有确定性 job_id**。所以"同一行被两轮同时选中"会投两个 job。幂等靠两层：
  a. 选行要求超龄，且每条重投后立即 `_bump_requeue_count` + `commit`（前移 updated_at）
     ⇒ **同一轮内**不重复选中；
  b. `REQUEUE_MAX_ATTEMPTS=3` 上限 + 超限置终态 ⇒ 不会无限重投烧外部 API。
  ⇒ **跨轮并发**（上一轮没跑完、timer 又拉起一轮）是 a 覆盖不到的缝隙 ——
    这正是 `yishu-requeue.service` 里 `flock -n` 存在的原因，别拆掉它。
"""
from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.queue import enqueue_high
from app.db.models import Content
from app.db.session import SessionLocal
from app.services.pipeline import process_content
from app.services.pipeline_common import (
    PROCESSING_STARTED_KEY,
    not_deleted,
    stale_since_condition,
)

logger = logging.getLogger("yishu.requeue")

# 超龄阈值：updated_at 超过该秒数视为卡死/滞留。ASR job_timeout=600s << 3600s，
# 正常在跑任务不会被误选；RQ 3 次退避重投总窗口（10+30+90s）也远小于该值。
REQUEUE_DEFAULT_STALE_SECONDS = 3600
# 单条内容重扫次数上限（防永久故障内容无限重投骚扰下游/计费）
REQUEUE_MAX_ATTEMPTS = 3
# 「本次处理开始时刻」的 extra 键 —— 定义在 services/pipeline_common.py（唯一来源，
# 避免本模块与 pipeline.py 循环导入）；此处 re-export 只是保持既有导入路径可用。
__all__ = [
    "PROCESSING_STARTED_KEY",
    "REQUEUE_DEFAULT_STALE_SECONDS",
    "REQUEUE_MAX_ATTEMPTS",
    "run_requeue",
    "stale_cutoff_condition",
    "stale_processing_count",
    "main",
]


def _stale_candidates(
    db: Session,
    cutoff: datetime,
    limit: int,
    include_empty_done: bool = False,
    only_empty_done: bool = False,
) -> list[Content]:
    """选中超龄 failed/processing 内容（updated_at 升序，最旧优先）。

    2026-10-16（台账 · caption 断供遗留）：`include_empty_done=True` 时**额外纳入
    「done 但成果为空」的照片** —— `status='done'` 而 `text` 为空，说明 caption 从未成功
    （视觉塔断供期间产生：真库实测 **1691 张** ✗✗）。这类"**假 done**"不在 failed/processing 里，
    本 job 原来看不见 ⇒ 它们对语义检索与跨模态聚类**永久隐身**（同 §7.20「done 却静默不索引」一族 ✓）。
    口径：**仅照片** + 同样要求**超龄**（防与正在跑的管线抢同一条）+ 需显式开启（默认关闭，
    保证既有调度语义与 CI 行为不变 ✓）。

    2026-10-19（caption 回填专项）：`only_empty_done=True` 时**只**选这类「假 done」照片。
     原因：真库当前还有 **4464 条 processing 滞留 + 336 条 failed**，`include-empty-done`
    会把它们一起选中 ⇒ 想单独回填 caption 时会被这些历史积压淹没（dry-run 实测 limit=5000
    全部被占满、1694 张目标里只见到一眼）。拆开开关后回填范围可精确计数、成本可预估。

    2026-10-25（D-23 · 4520 条卡死修复）：超龄判定改为**优先看 `extra.processing_started_at`**、
    回退 `updated_at` —— 判据收敛在 `stale_cutoff_condition()`（job 选行与 `/healthz` 计数共用同一份）。
    """
    from sqlalchemy import and_, or_

    empty_done = and_(
        Content.status == "done",
        Content.content_type == "photo",
        or_(Content.text.is_(None), Content.text == ""),
    )
    if only_empty_done:
        status_cond = empty_done
    else:
        status_cond = Content.status.in_(("failed", "processing"))
        if include_empty_done:
            status_cond = or_(status_cond, empty_done)
    return list(
        db.scalars(
            select(Content)
            .where(
                status_cond,
                stale_cutoff_condition(cutoff),
                not_deleted(),
            )
            .order_by(Content.updated_at)
            .limit(limit)
        ).all()
    )


def stale_cutoff_condition(cutoff: datetime):
    """「已超龄」的 SQL 判据 —— 委派给 `services/pipeline_common.stale_since_condition()`
    （**唯一来源**：job 选行与 `/healthz` 的 `stale_processing_count` 共用同一份口径；
    禁止在此另造判定，否则"兜底重投什么"与"健康检查报什么"会错位）。

    口径：`extra.processing_started_at` 存在且形状合法 ⇒ 用它（"本次真的跑了多久"）；
    不存在/不合法 ⇒ 回退 `updated_at`（历史行无该键，行为与修复前逐字一致）。
    为什么不能只用 `updated_at`：它带 `onupdate=now()`，任何写者（打标/情绪/聚合回写）
    碰一下就会把它刷成"刚刚" ⇒ 卡死行在旧口径下是"新鲜的"，兜底永远选不中。
    ⚠️ 解析必须用 `::timestamptz`（**不是** `to_timestamp`）——后者会丢弃 `+00:00` 偏移、
    按会话时区解释，实测恒偏 8 小时（本批测试当场抓出）。详见 `stale_since_condition`
    的 docstring；该处同时带正则形状守卫，防一个坏值把整批查询打死。
    """
    return stale_since_condition(cutoff)


def stale_processing_count(older_than_seconds: int = REQUEUE_DEFAULT_STALE_SECONDS) -> int:
    """当前超龄 processing 条数（供 `/healthz` 的 `stale_processing_count` 用）。

    与 `_stale_candidates` **同一判据**（都走 `pipeline_common.stale_processing_filter`），
    唯一差别是不取行、只计数（探活要轻，不能拉 5000 个 ORM 实例）。
    任何异常都向外抛 —— 由调用方（healthz）决定降级形态，本函数不吞错。
    """
    from app.services.pipeline_common import stale_processing_filter

    db: Session = SessionLocal()
    try:
        return int(
            db.scalar(
                select(func.count()).select_from(Content).where(stale_processing_filter(older_than_seconds))
            )
            or 0
        )
    finally:
        db.close()


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
    include_empty_done: bool = False,
    only_empty_done: bool = False,
    require_file: bool = False,
) -> dict:
    """执行一轮失败/卡死重扫，返回 {scanned, requeued, finalized, skipped_final, failed}

    - scanned: 本轮选中（超龄 failed/processing）数
    - requeued: 重新入队 process_content 数（重扫计数 +1）
    - finalized: 重扫超限置终态数
    - skipped_final: 终态失败（retryable=False）跳过数
    - failed: 单行处置异常数（保留原状，下轮重试）
    - include_empty_done: 额外纳入「done 但无 caption 的照片」（见 `_stale_candidates` ✓）。
      注意这些内容 `status='done'` ⇒ 不会被 `skipped_final` 分支拦住（那里要求 status=failed ✓），
      会正常走重投入队 ⇒ 管线重跑 caption（视觉守卫修好后可成功 ✓）。
    - only_empty_done: **只**选「done 但无 caption 的照片」（caption 回填专项）。优先级高于
      include_empty_done；用于把回填与 failed/processing 历史积压分开计数、分开控制成本。
    - require_file: 只保留**对象存储里真有文件**的行（caption 回填前置校验）。真库 1694 条
      「假 done」里 827 条根本没有 cos_key、753 条有 key 但文件不在盘上 ⇒ 不校验就重投，
      会把大量注定失败的行丢进管线（浪费 worker 时间，也会把 retryable 状态搅乱）。
    """
    db: Session = SessionLocal()
    cutoff = datetime.now(UTC) - timedelta(seconds=older_than_seconds)
    requeued = finalized = skipped_final = failed = 0
    try:
        candidates = _stale_candidates(
            db, cutoff, limit, include_empty_done=include_empty_done, only_empty_done=only_empty_done
        )
        if require_file:
            from app.services.external.storage import get_storage_backend

            storage = get_storage_backend()
            before = len(candidates)
            candidates = [
                c
                for c in candidates
                if (c.cos_key or "").strip() and storage.object_exists(c.cos_key)
            ]
            logger.info("require_file: 候选 %d → %d（无 cos_key / 文件不在盘上的行不重投）", before, len(candidates))
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
    parser.add_argument(
        "--include-empty-done",
        action="store_true",
        help="额外纳入「done 但无 caption 的照片」（caption 断供期间的历史遗留；默认关闭）",
    )
    parser.add_argument(
        "--only-empty-done",
        action="store_true",
        help="只选「done 但无 caption 的照片」（caption 回填专项；优先于 --include-empty-done）",
    )
    parser.add_argument(
        "--require-file",
        action="store_true",
        help="只保留对象存储里真有文件的行（回填前置校验；无 cos_key / 文件不在盘上则跳过）",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    report = run_requeue(
        older_than_seconds=args.older_than_seconds,
        limit=args.limit,
        dry_run=args.dry_run,
        include_empty_done=args.include_empty_done,
        only_empty_done=args.only_empty_done,
        require_file=args.require_file,
    )
    logger.info("requeue report: %s", report)
    print(report)


if __name__ == "__main__":
    main()
