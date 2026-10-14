"""W2-1 删除/回收站（2026-09-05）——独立 router（prefix /api/v1/trash）

设计要点（拆包前注释原文保留）：
- 软删只置 deleted_at/deleted_by（全库既有查询已过滤 deleted_at，零侵入）
- trash 独立 prefix router：/api/v1/trash 与 /api/v1/contents/{id} 路径段数不同零冲突

本模块由原单文件 app/api/contents.py 拆出（重构波 B2），端点逻辑未变。
"""
import logging
from datetime import datetime, timezone

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user
from app.core.errors import ERR_CONTENT_010, ApiError
from app.db.models import Content, User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.content import (
    TRASH_RETENTION_DAYS,
    ContentDeleteOut,
    TrashClearOut,
    TrashItemOut,
)
from app.services import content_purge, sync_writes

trash_router = make_router(prefix="/api/v1/trash", tags=["trash"])

# 2026-10-14（台账 §7.25①）：本模块此前无日志设施，而清空回收站的**云端清尾**
# （向量/原件）是尽力而为的 ⇒ 失败必须留痕。
logger = logging.getLogger("yishu.trash")


@trash_router.get("", response_model=ApiResponse[list[TrashItemOut]])
def trash_list(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """回收站列表（W2-1）：软删条目按删除时间倒序，days_left 由后端算好"""
    now = datetime.now(timezone.utc)
    rows = (
        db.execute(
            select(Content)
            .where(Content.user_id == user.id, Content.deleted_at.is_not(None))
            .order_by(Content.deleted_at.desc())
        )
        .scalars()
        .all()
    )
    items: list[TrashItemOut] = []
    for c in rows:
        deleted = c.deleted_at
        if deleted is None:  # 理论不可达（查询已过滤）；防御
            continue
        if deleted.tzinfo is None:
            deleted = deleted.replace(tzinfo=timezone.utc)
        days_left = max(0, TRASH_RETENTION_DAYS - (now - deleted).days)
        items.append(
            TrashItemOut(
                id=str(c.id),
                content_type=c.content_type,
                text=c.text,
                place=c.place,
                taken_at=c.taken_at,
                deleted_at=deleted,
                days_left=days_left,
            )
        )
    return ApiResponse(data=items)


@trash_router.post("/{content_id}/restore", response_model=ApiResponse[ContentDeleteOut])
def trash_restore(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """回收站恢复（W2-1 + 功能修复波 簇② D08-1/2 连带）：清权威表 + **反向清墓碑**
    + 中和审计日志

    `sync_writes.restore_content` 反向做齐三件事，缺一不可：
      ① 清 `contents.deleted_at/deleted_by`（回到正常内容域）
      ② 删 SFV 墓碑（否则同步域仍判该实体已删，他端 pull 到墓碑会再删一次）
      ③ pending `deleted_logs` → `restored`（否则 30 天后清理任务会把**已恢复**的内容
         彻底物理删除 —— 比不修更糟），并记一条"取消删除"变更日志供他端上抬镜像
    """
    row = db.execute(
        select(Content).where(
            Content.id == content_id,
            Content.user_id == user.id,
            Content.deleted_at.is_not(None),
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(ERR_CONTENT_010, "回收站中不存在该内容或无权访问", http=404)
    sync_writes.restore_content(db, row, user.id)
    db.commit()
    return ApiResponse(data=ContentDeleteOut(content_id=row.id, deleted=False, permanent_at=None))


@trash_router.delete("", response_model=ApiResponse[TrashClearOut])
def trash_clear(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """回收站清空（W2-1）：当前用户全部软删条目硬删（不可逆；客户端 showModal 强确认）。

    降级登记（_diff_ledger）：Qdrant 向量/COS 原件清理未挂接——MVP 只删 DB 行，
    向量残留不影响正确性（查询按 contents 行驱动）；挂接挂远期账 A3。
    """
    rows = (
        db.execute(
            select(Content).where(
                Content.user_id == user.id, Content.deleted_at.is_not(None)
            )
        )
        .scalars()
        .all()
    )
    # ⚠️ 清尾所需的 key 必须**在删除/提交之前**取出：ORM 行一旦被 delete+commit，再读属性会
    # 抛 `ObjectDeletedError`（2026-10-14 被新用例当场抓到——那会让接口在"DB 已删"之后 500）。
    purge_targets: list[tuple[str, str, str]] = [
        (str(r.id), r.cos_key or "", r.thumbnail_key or "") for r in rows
    ]
    cleared = 0
    for row in rows:
        db.delete(row)
        cleared += 1
    # 簇② 连带：硬删后清尾同步账本（删 SFV 行 + pending 审计日志标 done），
    # 否则 30 天后 run_cleanup 仍会选中悬空 pending 行空转一轮。
    ids = [t[0] for t in purge_targets]
    sync_writes.hard_delete_records(db, ids)
    db.commit()

    # 2026-10-14（台账 §7.25① · 批次 8 收口）：**真清空**。原实现只删 DB 行，Qdrant 向量与
    # 对象存储原件原样残留 ⇒ 用户以为"回收站已清空"，实际数据仍可被检索命中、仍占着存储
    # （**隐私承诺相关**）。清尾抽到 `services/content_purge.py`（30 天清理任务同一根因共用）。
    # ⚠️ 刻意放在 **DB 提交之后**：主提交失败时绝不能先去毁向量/原件；提交后清尾失败只留孤儿
    # （即原状），并如实回包 `warnings` —— 不假装"全部清干净"。
    warnings: list[str] = []
    vectors_purged = 0
    objects_purged = 0
    if ids:
        vectors_purged, vw = content_purge.purge_vectors(ids)
        warnings.extend(vw)
        objects_purged, ow = content_purge.purge_objects(purge_targets)
        warnings.extend(ow)
    if warnings:
        logger.warning("回收站清空：云端清尾未完全成功 %s", warnings)
    return ApiResponse(
        data=TrashClearOut(
            cleared=cleared,
            vectors_purged=vectors_purged,
            objects_purged=objects_purged,
            warnings=warnings,
        )
    )
