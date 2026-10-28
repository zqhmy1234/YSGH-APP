"""从用户已有记忆重建画像（P0-3 · 2026-10-28）。

设计：
- low 队列批处理，不阻塞 HTTP；
- 只处理本人 status=done 且有文本的内容；
- 复用 profile_annotator.annotate_content（不另造画像规则）；
- Content.extra["profile_rebuilt_at"] 作为内容级幂等标记；
- 每条失败隔离，不中断整批；
- RQ job.meta 暴露 total/processed/applied/pooled/failed/skipped；
- 无真实 LLM 时 degraded=true，由前端诚实展示“本地规则降级”。
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


def rebuild_user_profile(user_id: str, limit: int = 500) -> dict:
    """重建本人画像。返回稳定摘要，供 RQ job.return_value 与前端轮询。"""
    from rq import get_current_job

    job = get_current_job()
    degraded = not llm_available()
    summary = {
        "total": 0,
        "processed": 0,
        "applied": 0,
        "pooled": 0,
        "failed": 0,
        "skipped": 0,
        "degraded": degraded,
    }
    _progress(job, status="running", **summary)
    db = SessionLocal()
    try:
        rows = db.execute(
            select(Content)
            .where(
                Content.user_id == str(user_id),
                alive_clause(Content),
                Content.status == "done",
                Content.text.isnot(None),
                Content.text != "",
            )
            .order_by(Content.taken_at.asc(), Content.created_at.asc())
            .limit(limit)
        ).scalars().all()
        summary["total"] = len(rows)
        _progress(job, total=summary["total"])

        for content in rows:
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

        _progress(job, status="finished", **summary)
        return summary
    finally:
        db.close()
