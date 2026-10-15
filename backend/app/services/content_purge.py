"""内容「云端清尾」（Qdrant 向量 + 对象存储原件）—— 两处硬删路径共用（台账 §7.25①）。

为什么单独成模块：**同一根因散落两处**——
  · `api/contents/trash.py::trash_clear`（清空回收站）：只删 DB 行，**向量与原件全留着**；
  · `workers/cleanup_job.py`（30 天物理清理）：删对象与 DB 行，**向量没清**。
两者都会让用户以为"已经彻底删除"，而检索里仍能命中、对象仍占着存储（**隐私承诺相关**）。
集中在此，避免再各写一遍、也避免继续各自漏一半。

⚠️ 两条约定：
  1. **必须在 DB 硬删提交之后调用**——主提交失败时绝不能先去毁向量/原件（那会留下
     "内容还在、检索与文件没了"的更糟状态）；提交后清尾失败只会留下孤儿（即原状）。
  2. **只做尽力而为 + 计数/告警回传**，调用方决定怎么告知用户与记日志（不在这里静默）。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("yishu.purge")


def purge_vectors(content_ids: list[str]) -> tuple[int, list[str]]:
    """删这些内容的 Qdrant 点位；返回 `(请求删除条数, 告警)`（幂等，点位不存在静默忽略）"""
    if not content_ids:
        return 0, []
    try:
        from app.services.vector_store import get_store

        return get_store().delete_content_vectors(content_ids), []
    except Exception as exc:  # noqa: BLE001 —— 清尾失败不推翻"DB 已硬删"这一事实
        logger.warning("清尾：向量清理失败（%d 条）: %s", len(content_ids), exc)
        return 0, [f"向量未清理（{type(exc).__name__}）"]


def purge_objects(targets: list[tuple[str, str, str]]) -> tuple[int, list[str]]:
    """删对象存储原件，逐条尽力而为；返回 `(成功数, 告警)`。

    `targets` = `[(content_id, cos_key, thumbnail_key), …]` —— ⚠️ **刻意收纯数据而不是 ORM 行**：
    调用方（`trash_clear`）是"先删行 + 提交、后清尾"，而 ORM 行在删除提交后**不可再读属性**
    （`ObjectDeletedError`，2026-10-14 被新用例当场抓到）⇒ 所需字段必须在删除**前**取出。
    """
    from app.services.external.storage import get_storage_backend

    backend = get_storage_backend()
    done = 0
    failed = 0
    for _cid, cos_key, thumb_key in targets:
        for key in (cos_key, thumb_key):
            if not key:
                continue
            try:
                backend.delete_object(key)
                done += 1
            except Exception as exc:  # noqa: BLE001 —— 单条失败不影响其余
                failed += 1
                logger.warning("清尾：对象删除失败 key=%s: %s", key, exc)
    return done, ([f"对象未清理 {failed} 个"] if failed else [])


def delete_dependent_rows(db, content_ids: list[str]) -> int:
    """删**外键引用 `contents` 的从属行**——必须在硬删 contents **之前**调用（2026-10-15 · 台账 §7.28）。

    ## 为什么必须有

    端到端验证实测抓到：**只要用户对某条内容"纠错过"**（纠错链会写 `correction_log`），
    该内容进回收站后"清空回收站"就 **500**（`correction_log.content_id → contents.id` 的外键
    是 **NO ACTION**，DB 实证 `confdeltype='a'`；**全库只有这一张表引用 contents**）。
    同一序列里"提交纠错 → 清空"必挂、"不提交纠错 → 清空"必过 —— 这就是判据。

    ## 口径

    纠错记录含**用户原文**（`correction_log.text`）⇒ 彻底清除时**一并删除**：既消除 500，
    也符合"彻底清除"的隐私承诺（否则内容删了、纠错原文还在库里，与用户对"清空"的理解不符）。

    ⚠️ 只删这一张表是因为**实测全库只有它引用 `contents`**；将来若新增引用表，
    必须在此同步补上（否则同一 500 会以"某功能用完就删不掉"的形态复现）。
    """
    from sqlalchemy import bindparam, text

    ids = [str(i) for i in content_ids]
    if not ids:
        return 0
    stmt = text("DELETE FROM correction_log WHERE content_id::text IN :ids").bindparams(
        bindparam("ids", expanding=True)
    )
    result = db.execute(stmt, {"ids": ids})
    return int(result.rowcount or 0)
