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


# ── D-23（2026-10-25 · 4520 条 processing 卡死修复）────────────────────────────
# 「本次处理开始时刻」的 extra 键。**定义在此模块**（而不是 pipeline.py）是刻意的：
# `workers/requeue_job.py` 需要它，而 requeue_job 已经 `from app.services.pipeline
# import process_content` —— 若把常量放 pipeline.py，requeue_job 再反向 import 常量
# 就构成循环导入。本模块是"无业务依赖的共享底座"（见文件头），正是为此而存在。
PROCESSING_STARTED_KEY = "processing_started_at"


def not_deleted():
    """软删过滤（`Content` 未删除）的**唯一手抄点**。

    为什么收在这里：软删过滤全仓散布 40+ 处，`scripts/audit_harness.py` 的
    `dup_patterns.soft_delete_filter` 棘轮就是盯这个（手抄一处涨一处就红）。
    D-23 需要新的"超龄 processing"判据（也带软删过滤）——按门禁的指引走公共 helper，
    而不是再手抄一份：本函数是落点，`stale_processing_filter` 与
    `workers/requeue_job._stale_candidates` 都经由它。
    """
    from app.db.models import Content

    return Content.deleted_at.is_(None)


def transition_processing(content: Content, target: str) -> None:
    """管线生命周期状态转换 —— **唯一入口**（置 status + 维护 `processing_started_at`）。

    ## 为什么收成一个函数（而不是 begin/finish 两个）

    不变式是**成对**的：「`status == 'processing'` ⇔ 有本次开始时刻」。
    拆成两个函数，就允许"只置状态忘了打点"或"只清打点忘了置状态"这类半截写法 ——
    而本批修的 4520 条卡死，正是"状态被置了、但没有任何机制能看出它跑了多久"的形态。
    收成一个转换入口后，不变式由函数内部保证，调用方只能选目标状态。

    | target     | status       | processing_started_at |
    |------------|--------------|-----------------------|
    | processing | processing   | **写入**（本次开始时刻） |
    | done       | done         | 清除 |
    | failed     | failed       | 清除 |

    为什么需要那个时刻（而不是用 `updated_at`）：`Content.updated_at` 带
    `onupdate=now()`，**任何**写者（`tag_content` 打标、`enrich_content_emotion`
    情绪增强、聚合任务）碰一下这行都把它刷成"刚刚"。于是「3 天前卡死、今天被标签
    任务顺手写了一下」的行在 `updated_at` 口径下是**新鲜的** ⇒ 兜底永远选不中它。
    本键只由本函数写/清，才是"是否真有人在跑"的唯一真值。

    调用方负责 `db.commit()`（阶段 0 的分阶段提交语义不变）。
    """
    from datetime import UTC
    from datetime import datetime as _dt

    content.status = target
    extra = dict(content.extra or {})
    if target == "processing":
        extra[PROCESSING_STARTED_KEY] = _dt.now(UTC).isoformat()
    else:
        # 收尾必须清：残留打点会让排障时"这行到底跑没跑过"有两种读法；
        # 且 status 若将来被别的路径改回 processing，陈旧打点会立刻把它判成超龄。
        extra.pop(PROCESSING_STARTED_KEY, None)
    content.extra = extra


def stale_since_condition(cutoff):
    """**纯"已超龄"谓词**（不含 status/软删，供不同场景组合）—— 本仓唯一来源。

    口径（2026-10-25 · D-23 · 4520 条卡死修复）：
      `extra -> 'processing_started_at'` **存在且可解析** ⇒ 用它判（"本次真的跑了多久"）；
      **不存在/不可解析** ⇒ 回退 `updated_at`（历史行没有该键，行为与修复前逐字一致）。

    为什么需要这个键（而不是继续只用 `updated_at`）：
      `Content.updated_at` 带 `onupdate=now()`，**任何**写者（`tag_content` 打标回写、
      `enrich_content_emotion` 情绪回写、聚合任务）只要碰这一行就把它刷成"刚刚"。
      于是「3 天前卡死、今天被标签任务顺手写了一下」的行在 `updated_at` 口径下是**新鲜的**
      ⇒ 兜底永远选不中它（这正是这次 4520 条长期静默的一条隐蔽成因）。

    ## ⚠️ 解析方式：必须用 `::timestamptz`，**不能用 `to_timestamp(str, fmt)`**

    本函数第一版用 `func.to_timestamp(started, 'YYYY-MM-DD"T"HH24:MI:SS.US')`，
    本批测试 `test_fresh_stamp_shields_running_row` **当场抓出它是错的**：
    `to_timestamp` 把 `+00:00` 偏移当**装饰**直接丢弃，按会话时区（本机 Asia/Shanghai）
    解释墙钟时间 ⇒ 实测**恒定偏差 480 分钟（8 小时）**。后果有两个，都很难看：
      · 打点"3 天前"的行算出来只老 2 天 16 小时 —— 影响有限（仍超龄，蒙对）；
      · 打点"刚刚"的**正在跑**的行算出来老了 8 小时 ⇒ **被判超龄** ⇒ 兜底会去抢
        正在跑的行 ⇒ 双投 + 双份外部 API 计费。
    `::timestamptz` 会**正确读取偏移**（实测偏差 0 分钟），故改用它。

    ## 为什么还要正则守卫

    `::timestamptz` 遇到**非法字符串会抛异常**（PG 无 TRY_CAST）。若某一行被人工/外部
    写入了坏值，整个查询就会失败 —— 而 `/healthz` 会把 count 报成 -1、
    `requeue_job` 会**整批崩掉**（4000+ 条卡死行被**一个**坏值全部堵死，这是比原故障更糟的
    失效形态）。故先用正则确认形状，形状不对就走 `updated_at` 回退；
    SQL 的 CASE 是短路的，不合法分支不会被 cast。
    """
    from sqlalchemy import TIMESTAMP, cast, false, func, or_

    from app.db.models import Content

    started = Content.extra[PROCESSING_STARTED_KEY].astext
    # 形状校验：只认 transition_processing 写出的形态（YYYY-MM-DDTHH:MM:SS[.ffffff][+HH:MM]）
    # `coalesce(..., false)`：started 为 NULL 时 `~` 结果是 NULL，必须显式落成 false，
    # 否则 `or_` 的三值逻辑会让整条件变成 NULL（NULL 在 WHERE 里等于 false，会静默丢行）。
    shape_ok = func.coalesce(started.op("~")(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"), false())
    # 短路求值靠 CASE：shape_ok 为 false 的行不会执行这个 cast（不做无用功也不会抛错）
    stamp_is_stale = cast(started, TIMESTAMP(timezone=True)) <= cutoff
    return or_(
        # ① 有打点且形状合法 ⇒ 用打点（唯一真值）
        shape_ok & stamp_is_stale,
        # ② 无打点/形状不合法 ⇒ 回退 updated_at（与修复前行为一致）
        (~shape_ok) & (Content.updated_at <= cutoff),
    )


def stale_processing_filter(older_than_seconds: int = 3600):
    """「超龄 processing」判据 —— **唯一来源**。

    `workers/requeue_job.stale_cutoff_condition()` 与 `/healthz` 的
    `stale_processing_count` 都经由本函数（前者直接用 `stale_since_condition` 组合
    failed/processing 两种 status），确保"兜底重投什么"与"健康检查报什么"永远是同一批行
    —— 两套口径会让告警对象与修复对象错位，是本类静默故障的经典复发形态。
    """
    from datetime import UTC, timedelta
    from datetime import datetime as _dt

    from sqlalchemy import and_

    from app.db.models import Content

    cutoff = _dt.now(UTC) - timedelta(seconds=older_than_seconds)
    return and_(
        Content.status == "processing",
        not_deleted(),
        stale_since_condition(cutoff),
    )



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
