"""W2-1 删除/回收站（2026-09-05）——独立 router（prefix /api/v1/trash）

设计要点（拆包前注释原文保留）：
- 软删只置 deleted_at/deleted_by（全库既有查询已过滤 deleted_at，零侵入）
- trash 独立 prefix router：/api/v1/trash 与 /api/v1/contents/{id} 路径段数不同零冲突

本模块由原单文件 app/api/contents.py 拆出（重构波 B2），端点逻辑未变。
"""
import logging
from datetime import UTC, datetime

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


def _ensure_l1_event_after_restore(db: Session, row: Content, user_id) -> None:
    """恢复内容后，保证它在时间轴上有一张 L1 日卡（2026-11-01 · 走查 9.2）。

    ## 为什么需要
    删除内容时会把「成员被摘空」的 L1 事件一并删掉（见
    `contents/__init__.py::_reconcile_events_after_content_delete`）；
    而恢复只把 `contents.deleted_at` 置空 ⇒ 内容复活但**没有卡片**，
    用户点「恢复」后回到时间轴什么都看不到（设备实测：回收站 3→2 条，时间轴仍 3 条）。

    ## 做法
    若该内容已属于某个**存活成员关系**，什么都不做（正常路径）。
    否则按端侧同一幂等键 `ev-c-<contentId>` 重建 L1 事件 + 成员，口径与客户端
    `submitContentAsL1` 一致 ⇒ 重放/再次删除都不会产生重复卡。

    ## 封面
    只有照片才把 `cover_content_id` 指向自己；文字/语音留空（客户端会回退到
    `open_content_id` 跳转，不依赖封面）。
    """
    from app.db.alive import alive
    from app.db.models import Event, EventItem

    alive = db.execute(
        select(EventItem.event_id)
        .join(Event, Event.id == EventItem.event_id)
        .join(Content, Content.id == EventItem.content_id)
        .where(EventItem.content_id == row.id, alive(Content))
    ).scalar_one_or_none()
    if alive is not None:
        return

    ts = row.created_at or datetime.now(UTC)
    day = ts.astimezone().strftime("%Y-%m-%d")
    kind = "照片" if row.content_type == "photo" else ("语音" if row.content_type == "voice" else "文字")
    excerpt = (row.text or "").strip().replace("\n", " ")[:14]
    title = f"{day} · {kind}" + (f" · {excerpt}" if excerpt else "")

    ev = Event(
        user_id=user_id,
        level=1,
        title=title,
        title_source="device",
        start_time=ts,
        end_time=ts,
        confidence=0.9,
        status="draft",
        generated_by="device",
        client_event_id=f"ev-c-{row.id}",
        cover_content_id=str(row.id) if row.content_type == "photo" else None,
    )
    db.add(ev)
    db.flush()
    db.add(EventItem(content_id=str(row.id), event_id=ev.id))
    logger.info("恢复内容 %s 后重建 L1 事件 %s（%s）", row.id, ev.id, title)


@trash_router.get("", response_model=ApiResponse[list[TrashItemOut]])
def trash_list(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """回收站列表（W2-1）：软删条目按删除时间倒序，days_left 由后端算好"""
    now = datetime.now(UTC)
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
            deleted = deleted.replace(tzinfo=UTC)
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
    # 2026-11-01（走查 9.2 实测缺陷）：**恢复的内容回不到时间轴**。
    # 现场：删 sunrise-walk-107 → 回收站点「恢复」→ `POST /trash/{id}/restore` 200、
    # 回收站条目消失，但时间轴仍是 3 条、这张卡没回来。
    # 根因：删除时（`_reconcile_events_after_content_delete`）会把"成员被摘空"的 L1 事件
    # 一并删掉；而恢复只把 `contents.deleted_at` 置空，**不会重建事件** ⇒ 内容活着但没有卡片。
    # 处置：恢复后若该内容不属于任何存活事件，按端侧同一幂等键 `ev-c-<contentId>` 重建
    # L1 日卡（与客户端提交口径一致，重放/再次删除都不会产生重复卡）。
    _ensure_l1_event_after_restore(db, row, user.id)
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
    # 2026-10-15（台账 §7.28）：**先删从属行**——`correction_log` 对 `contents` 是 NO ACTION
    # 外键，用户"纠错过"的内容硬删会 500（端到端验证实测：提交纠错的序列必挂）。
    content_purge.delete_dependent_rows(db, [t[0] for t in purge_targets])
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
