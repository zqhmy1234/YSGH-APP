"""从用户已有记忆重建画像（P0-3 · 2026-10-28）。

设计：
- low 队列批处理，不阻塞 HTTP；
- 只处理本人 status=done 且有文本的内容；
- 复用 profile_annotator.annotate_content（不另造画像规则）；
- Content.extra["profile_rebuilt_at"] 作为内容级幂等标记；
- 每条失败隔离，不中断整批；
- RQ job.meta 暴露 total/processed/applied/pooled/failed/skipped/batches；
- 无真实 LLM 时 degraded=true，由前端诚实展示“本地规则降级”。
- 2026-10-30（P2 F-6）：批大小与总量解耦——`limit` 是**每批条数**，总量走键集分页
  遍历到底（此前 `limit` 兼作总量上限 ⇒ 超 500 条的画像静默只重建最早 500 条）。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select

from app.db.models import Content
from app.db.session import SessionLocal
from app.services.llm_ops.base import llm_available
from app.services.profile_annotator import annotate_content
from app.services.query_helpers import alive_clause

logger = logging.getLogger("yishu.profile_rebuild")


def _progress(job, **values) -> None:
    if job is None:
        return
    meta = dict(job.meta or {})
    meta.update(values)
    job.meta = meta
    job.save_meta()


def _scope_clauses(user_id: str) -> list:
    """重建范围（**单一真源**：count 与实际遍历必须同口径，否则进度条永远对不上）"""
    return [
        Content.user_id == str(user_id),
        alive_clause(Content),
        Content.status == "done",
        Content.text.isnot(None),
        Content.text != "",
    ]


def rebuild_user_profile(user_id: str, limit: int = 500) -> dict:
    """重建本人画像。返回稳定摘要，供 RQ job.return_value 与前端轮询。

    2026-10-30（P2 F-6）：`limit` 的语义由「**总量上限**」改为「**每批条数**」。

    改造前是 `.limit(limit)`（默认 500）后一次性取回 ⇒ 内容多于 500 条的用户，
    第 501 条起的记忆**永远不参与画像重建**，而 job 仍 `status=finished` +
    `total=500`（看起来"全处理完了"）。这是静默截断：用户看不到任何信号，
    "重建画像"对老用户等于只重算了最早 500 条。

    现改为键集分页（`id > last_id`）遍历全部：`total` 用 count 查**真实总数**，
    `processed` 实际推进 ⇒ 进度与事实一致。遍历序由 `taken_at` 改为 `id`：
    批处理顺序对结果无影响（每条独立标注、幂等标记在 `extra.profile_rebuilt_at`），
    而 `taken_at` 可空/可重复，拿它做键集会在 NULL 段漏行——漏行比乱序更糟。
    没有总量上限：跑不完由 RQ 的 `job_timeout`（3600s，见 api/profile.py）兜底成
    `failed`，那是**显式失败**，不是"完成"。
    """
    from rq import get_current_job
    from sqlalchemy import func

    job = get_current_job()
    degraded = not llm_available()
    batch_size = max(1, int(limit))
    summary = {
        "total": 0,
        "processed": 0,
        "applied": 0,
        "pooled": 0,
        "failed": 0,
        "skipped": 0,
        "batches": 0,
        "degraded": degraded,
    }
    _progress(job, status="running", **summary)
    db = SessionLocal()
    try:
        summary["total"] = int(
            db.execute(
                select(func.count()).select_from(Content).where(*_scope_clauses(user_id))
            ).scalar_one()
        )
        _progress(job, total=summary["total"])

        last_id: str | None = None
        while True:
            stmt = select(Content).where(*_scope_clauses(user_id))
            if last_id is not None:
                stmt = stmt.where(Content.id > last_id)
            batch = db.execute(
                stmt.order_by(Content.id.asc()).limit(batch_size)
            ).scalars().all()
            if not batch:
                break
            _process_batch(db, job, user_id, batch, summary)
            summary["batches"] += 1
            last_id = str(batch[-1].id)
            _progress(job, batches=summary["batches"], processed=summary["processed"])
            if len(batch) < batch_size:
                break

        _progress(job, status="finished", **summary)
        return summary
    finally:
        db.close()


def _process_batch(db, job, user_id: str, batch: list, summary: dict) -> None:
    """逐条标注（单条失败隔离，不中断整批）；就地推进 summary 与 job.meta。"""
    for content in batch:
        extra = dict(content.extra or {})
        if extra.get("profile_rebuilt_at"):
            summary["skipped"] += 1
            summary["processed"] += 1
            _progress(
                job,
                processed=summary["processed"],
                skipped=summary["skipped"],
            )
            continue
        try:
            result = annotate_content(db, content)
            extra["profile_rebuilt_at"] = datetime.now(UTC).isoformat()
            content.extra = extra
            db.commit()
            summary["applied"] += len(result.get("applied") or [])
            summary["pooled"] += len(result.get("pooled") or [])
        except Exception:  # noqa: BLE001 —— 单条失败隔离，继续其余记忆
            db.rollback()
            summary["failed"] += 1
            logger.warning(
                "画像重建单条失败 user=%s content=%s",
                user_id,
                content.id,
                exc_info=True,
            )
        summary["processed"] += 1
        _progress(
            job,
            processed=summary["processed"],
            applied=summary["applied"],
            pooled=summary["pooled"],
            failed=summary["failed"],
            skipped=summary["skipped"],
        )
