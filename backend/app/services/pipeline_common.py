"""内容 AI 管线 · 共享底座（extra JSON 样板 / 日志 / 聚合入队）

波D ①（2026-09-10）自 `pipeline.py` 逐字拆出（**只搬代码不改逻辑**）：
本模块提供**无业务依赖**的跨模块共享件，被 pipeline.py 及同批拆出的
`pipeline_audio` / `pipeline_photo` / `pipeline_tagging` 共同引用
（放此处而非 pipeline.py，是为了避免"卫星模块反向 import pipeline"造成循环导入）。

⚠️ 本模块**不是** monkeypatch 目标（`tests/test_pipeline.py` 只 patch
`app.services.pipeline.<name>`），故可安全承载被多模块引用的样板函数。
"""
from __future__ import annotations

import logging

from app.db.models import Content

# 与 pipeline.py 同 logger 名（logging.getLogger 按名注册 → 同一 logger 对象，日志通道不变）
logger = logging.getLogger("yishu.pipeline")


def patch_extra(content: Content, **updates) -> None:
    """extra JSON 列拷贝-合并-回写样板收敛（TD-P2B · S1-M6：原 7 处内联
    `extra = dict(content.extra or {}); extra[...] = ...; content.extra = extra`）"""
    extra = dict(content.extra or {})
    extra.update(updates)
    content.extra = extra


def extra_get(content: Content, key: str, default=None):
    """读取侧样板收敛：`(content.extra or {}).get(key, default)`"""
    return (content.extra or {}).get(key, default)


def _enqueue_user_aggregation(user_id: str) -> str:
    """F3/R5-3：按 user 级 key 入队聚合独立 RQ 任务（同用户同时多内容只跑一次）

    core/queue.enqueue_unique 的 Redis SETNX 原子预占位做去重合并：
      - job_id = run_user_aggregation_user_<uid>（确定性，同用户并发/重复触发不重复入队）
      - 聚合任务扫描该用户全部未成候选内容（含本批并发内容），一次覆盖并发批次
      - 放 low 队列（P2-P4 聚合/批量），DEFAULT_JOB_TIMEOUT（300s）

    返回 "queued"（已入队）/ "enqueue_failed"（入队失败，调用方只记日志不否定主结果）。
    """
    try:
        from app.core.queue import DEFAULT_JOB_TIMEOUT, QUEUE_LOW, enqueue_unique
        from app.services.events import run_user_aggregation

        enqueue_unique(
            run_user_aggregation,
            f"user:{user_id}",
            str(user_id),
            mode="l2l3",
            queue_name=QUEUE_LOW,
            job_timeout=DEFAULT_JOB_TIMEOUT,
        )
        return "queued"
    except Exception as exc:  # noqa: BLE001 —— 入队失败不影响主转写结果
        logger.warning("聚合任务入队失败 user=%s: %s", user_id, type(exc).__name__)
        return "enqueue_failed"
