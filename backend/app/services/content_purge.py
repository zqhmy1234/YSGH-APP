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
