"""内容生命周期 · 同步写入**单一路径**（功能修复波 簇② · D08-1/2/3/5 · 2026-09-25）

缺陷原文见 `.trae/specs/refactor-extensibility-maintainability-wave/audit/domain-08-sync-offline.md`。

软删此前是**两条互不相通的轨道**，"保留 30 天"的三条承诺（立即隐藏 / 可恢复 /
30 天后彻底清除）在任何一条用户路径上都无法同时成立：

  - REST `DELETE /contents/{id}`：只置 `contents.deleted_at`，**不写 `deleted_logs`**
    ⇒ `run_cleanup` 只认 `deleted_logs` ⇒ **永不彻底清除**（D08-2）。
  - sync `push_ops(op_type=delete)`：只写 SFV 墓碑 + `deleted_logs`，**不置
    `contents.deleted_at`** ⇒ REST 列表/详情（`load_alive_content`）**仍视为存活**，
    30 天后又被 job 物理删 ⇒「先删了还在，后突然永久消失」（D08-1）；
    且回收站（按 `deleted_at` 过滤）也看不到它 ⇒ 无法恢复。
  - sync `push_ops(op_type=upsert_field)`：只写 SFV，**从不投影回权威表**
    ⇒ 离线改备注"上云成功（applied+1）"但用户永远看不到（D08-3）。
  - LWW 冲突分支没有 `continue`，控制流落到变更日志写入 ⇒ 把**被判败的客户端旧值**
    原样写进增量拉取源，污染其他端的 replay（D08-5）。

本模块把这些写入收成**单一语义路径**：

  - `finalize_soft_delete()` —— "软删对用户可见状态 / 审计 / 其他端"做什么，**只有这一处定义**；
  - `project_field()` —— 白名单字段写回权威表；
  - `restore_content()` —— 恢复 = 反向清墓碑 + 中和审计日志（防 30 天后误彻底清除）。

REST 端点与 `push_ops` 两处**同调这些原语**；唯一差异是 SFV 墓碑本身的写法
（`push_ops` 必须走批内预取映射 `sfv_by_key`，以保 R2#6「批内同键不 500」语义）。

时间口径（有意分开）：
  - `change_ts` = LWW/账本时间戳（`push_ops` 用**客户端时钟** `client_ts`，与 SFV 墓碑
    `updated_at` 同源；否则 `reconcile` 会误报分歧）；
  - `anchor`   = 保留期/审计锚点（**服务端时钟**）——`trash_list` 的 `days_left` 与
    `run_cleanup` 的 30 天窗口都读它，不能被设备时钟带偏。

字段同步白名单（D08-3 收敛）：content 域客户端当前唯一会在离线改的字段是 `remark`。
**当实体存在权威行时**，白名单外字段一律 `rejected`（不静默写 SFV 假装成功——否则
又回到"applied 计数 +1 但用户看不到"的谎）；无权威行的实体（历史/合成/未来类型）
沿用通用 SFV 账本语义，不在本波收窄（见 `SYNC-018` 记录与暗物质审计同族教训）。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.db.models import Content, DeletedLog, OfflineQueue, SyncFieldVersion
from app.services.sync_common import TOMBSTONE_FIELD

# 服务端发起变更时的记账 device_id（非客户端设备；前缀下划线避免与真实设备 id 撞名）
SERVER_DEVICE = "_server"

# 服务端 → 客户端「取消删除」变更类型（客户端 applyChanges 按"非 delete ⇒ 存活"处理）
OP_RESTORE = "restore"

# 服务端 → 客户端「新建」变更类型（D08-18；客户端 applyChanges 只区分 delete，非 delete ⇒ 存活）
OP_CREATE = "create"

# ── content 域可同步字段白名单：字段名 → 期望值类型（D08-3）──
# 当前客户端唯一在用的是 remark（client/utils/play_content.uts::updateContentRemark →
# enqueueFieldOp('content', id, 'remark', ...)）。新增字段须在此登记（含类型），
# 否则对权威行存在的 content 会被 rejected。
CONTENT_SYNC_FIELDS: dict[str, type] = {"remark": str}

# 已收敛白名单的 entity_type；未登记者沿用通用账本语义（本波不收窄）
WHITELISTED_ENTITY_TYPES = frozenset({"content"})


def is_syncable_field(entity_type: str, field: str) -> bool:
    """该 (实体类型, 字段) 是否在同步白名单内（未收敛的类型一律放行）"""
    if entity_type not in WHITELISTED_ENTITY_TYPES:
        return True
    return field in CONTENT_SYNC_FIELDS


def is_field_value_ok(entity_type: str, field: str, value) -> bool:
    """白名单字段的值类型校验（防把 dict/list 绑进 Text 列触发 DB 层 500）"""
    if entity_type not in WHITELISTED_ENTITY_TYPES:
        return True
    expected = CONTENT_SYNC_FIELDS.get(field)
    if expected is None:
        return True
    return value is None or isinstance(value, expected)


def projection_column(entity_type: str, field: str) -> str | None:
    """白名单字段 → 权威表列名（白名单即列名约定；非白名单/未收敛类型 → None）"""
    if entity_type not in WHITELISTED_ENTITY_TYPES:
        return None
    return field if field in CONTENT_SYNC_FIELDS else None


def project_field(entity_type: str, content: Content | None, field: str, value) -> bool:
    """把白名单字段写回 Content 权威行；返回是否真的投影（False = 调用方须显式处理）

    只做投影本身；值类型前置校验见 `is_field_value_ok`（调用方在 rejection 分支用）。
    """
    col = projection_column(entity_type, field)
    if col is None or content is None:
        return False
    setattr(content, col, value)
    return True


def log_change(
    db: Session,
    user_id: str,
    device_id: str,
    *,
    op_type: str,
    entity_type: str,
    entity_id: str,
    updated_at: datetime,
    field: str | None = None,
    value=None,
    op_id: str | None = None,
) -> str:
    """写增量拉取变更日志（`offline_queue`：其他端 replay 的唯一来源）

    payload 形状与 `push_ops` / `events/sync.py` 既有写法逐字一致（客户端
    `applyChanges` 只消费 `op_type`/`entity_id`/`updated_at`，故 `updated_at` 必须非空）。
    `op_id` 由调用方给定（`push_ops` 传客户端 op_id 以保幂等语义），缺省服务端生成。
    """
    op_id = op_id or f"srv-{uuid.uuid4().hex}"
    db.add(
        OfflineQueue(
            op_id=op_id,
            user_id=user_id,
            device_id=device_id,
            op_type=op_type,
            payload={
                "op_type": op_type,
                "entity_type": entity_type,
                "entity_id": entity_id,
                "field": field,
                "value": value,
                "updated_at": updated_at.isoformat(),
            },
            status="done",
        )
    )
    return op_id


def ensure_deleted_log(
    db: Session, content_id: str, user_id: str, anchor: datetime
) -> DeletedLog:
    """幂等写 `deleted_logs`（30 天清理任务的唯一选取源）

    同一内容已有 pending 行则复用——防「REST 删 + 离线删」或重删写出多行、
    被 `run_cleanup` 重复选中（第二次是空转，但仍会多占一轮扫描）。
    """
    existing = db.execute(
        select(DeletedLog)
        .where(DeletedLog.content_id == content_id, DeletedLog.cleanup_status == "pending")
        .order_by(DeletedLog.id.desc())
    ).scalars().first()
    if existing is not None:
        return existing
    row = DeletedLog(
        content_id=content_id, deleted_by=user_id, deleted_at=anchor, cleanup_status="pending"
    )
    db.add(row)
    return row


def upsert_tombstone(
    db: Session, entity_type: str, entity_id: str, user_id: str, change_ts: datetime
) -> SyncFieldVersion:
    """单行 SFV 墓碑 upsert（REST 入口用；`push_ops` 走批内映射，语义相同）"""
    row = db.execute(
        select(SyncFieldVersion).where(
            SyncFieldVersion.entity_type == entity_type,
            SyncFieldVersion.entity_id == entity_id,
            SyncFieldVersion.field == TOMBSTONE_FIELD,
        )
    ).scalar_one_or_none()
    if row is None:
        row = SyncFieldVersion(
            entity_type=entity_type,
            entity_id=entity_id,
            field=TOMBSTONE_FIELD,
            user_id=user_id,
        )
        db.add(row)
    row.deleted = True
    row.updated_at = change_ts
    return row


def write_field_version(
    db: Session,
    user_id: str,
    *,
    entity_type: str,
    entity_id: str,
    field: str,
    value,
    updated_at: datetime,
) -> SyncFieldVersion:
    """REST 权威写入的**账本同步**（双向写穿）：单行 SFV upsert，无 LWW

    入参即云端权威值（用户在线的直接操作，"用户操作优先"），故不做 LWW 比较——
    但写出的 `updated_at` 会成为**后续离线编辑的 LWW 基准**（缺它则离线旧值
    会盖掉在线新值，见本模块头注 D08-3 的 LWW 基准一面）。
    """
    row = db.execute(
        select(SyncFieldVersion).where(
            SyncFieldVersion.entity_type == entity_type,
            SyncFieldVersion.entity_id == entity_id,
            SyncFieldVersion.field == field,
        )
    ).scalar_one_or_none()
    if row is None:
        row = SyncFieldVersion(
            entity_type=entity_type, entity_id=entity_id, field=field, user_id=user_id
        )
        db.add(row)
    row.value = value
    row.updated_at = updated_at
    row.deleted = False
    return row


def finalize_soft_delete(
    db: Session,
    user_id: str,
    *,
    entity_type: str,
    entity_id: str,
    change_ts: datetime,
    anchor: datetime | None = None,
    content: Content | None = None,
    device_id: str = SERVER_DEVICE,
    op_id: str | None = None,
) -> None:
    """**软删的唯一语义定义**：权威表投影 + 幂等审计日志 + 变更日志

    调用方必须先写好 SFV 墓碑（REST 经 `upsert_tombstone`；`push_ops` 经批内映射）。
    """
    anchor = anchor or datetime.now(timezone.utc)
    if entity_type == "content" and content is not None:
        # D08-1：权威表置删 ⇒ REST 列表/详情立即 404，回收站可见可恢复
        content.deleted_at = anchor
        content.deleted_by = user_id
    # D08-2：写审计日志 ⇒ 30 天清理任务真的会选中它
    ensure_deleted_log(db, entity_id, user_id, anchor)
    log_change(
        db,
        user_id,
        device_id,
        op_type="delete",
        entity_type=entity_type,
        entity_id=entity_id,
        updated_at=change_ts,
        op_id=op_id,
    )


def soft_delete_content(
    db: Session,
    content: Content,
    user_id: str,
    *,
    change_ts: datetime | None = None,
    anchor: datetime | None = None,
    device_id: str = SERVER_DEVICE,
) -> None:
    """REST `DELETE /contents/{id}` 的单一软删入口（写墓碑 + `finalize_soft_delete`）"""
    change_ts = change_ts or datetime.now(timezone.utc)
    upsert_tombstone(db, "content", str(content.id), user_id, change_ts)
    finalize_soft_delete(
        db,
        user_id,
        entity_type="content",
        entity_id=str(content.id),
        change_ts=change_ts,
        anchor=anchor,
        content=content,
        device_id=device_id,
    )


def log_content_created(
    db: Session,
    user_id: str,
    content_id: str,
    *,
    when: datetime | None = None,
    device_id: str = SERVER_DEVICE,
) -> str:
    """**新建内容**的变更日志（D08-18 · 端间一致性）。

    原状：变更日志只在 `push_ops`（客户端离线改/删）与 REST 改/删时写入，
    **创建不写** ⇒
      · 他端 `pull_changes` 无源（该实体永不出现在增量流里）；
      · `reconcile` 的 `missing_on_client` 能报出差异，客户端却**没有可重放的 op**
        ⇒ 用户在两台设备上都开了 App 时，A 端新建的记忆在 B 端"看不见也补不上"。

    修法：创建路径统一调本函数（单一出口）。`updated_at` 用**服务端时钟**
    （创建是服务端权威事件；客户端镜像只消费 `updated_at` 做展示/对账）。

    本函数**自带提交**：调用时权威行已落库（各创建路径都先 commit），此处是创建后的
    记账动作；记账失败不应回滚已建内容，但**必须让调用方看见**（不吞异常）。
    """
    op_id = log_change(
        db,
        user_id,
        device_id,
        op_type=OP_CREATE,
        entity_type="content",
        entity_id=str(content_id),
        updated_at=when or datetime.now(timezone.utc),
    )
    db.commit()
    return op_id


def restore_content(
    db: Session,
    content: Content,
    user_id: str,
    *,
    when: datetime | None = None,
    device_id: str = SERVER_DEVICE,
) -> None:
    """恢复（`POST /trash/{id}/restore`）：清权威表 + **反向清墓碑** + 中和审计日志

    两处反向动作缺一不可：
      - 清 SFV 墓碑：否则同步域仍判该实体已删（他端 pull 到墓碑会再删一次）；
      - pending 审计日志 → `restored`：否则 30 天后 `run_cleanup` 会**把已恢复的内容
        彻底物理删除**（比不修更糟）。
    """
    when = when or datetime.now(timezone.utc)
    content.deleted_at = None
    content.deleted_by = None
    entity_id = str(content.id)
    db.execute(
        delete(SyncFieldVersion).where(
            SyncFieldVersion.entity_type == "content",
            SyncFieldVersion.entity_id == entity_id,
            SyncFieldVersion.field == TOMBSTONE_FIELD,
        )
    )
    db.execute(
        update(DeletedLog)
        .where(DeletedLog.content_id == entity_id, DeletedLog.cleanup_status == "pending")
        .values(cleanup_status="restored")
    )
    # 变更日志：取消删除（客户端按"非 delete ⇒ 存活"上抬镜像）
    log_change(
        db,
        user_id,
        device_id,
        op_type=OP_RESTORE,
        entity_type="content",
        entity_id=entity_id,
        updated_at=when,
    )


def hard_delete_records(db: Session, content_ids: list[str]) -> None:
    """硬删（`trash_clear`）后的账本清尾：删 SFV 行 + 审计日志标 done

    内容行已物理删除 ⇒ 其 SFV/审计日志已无对账意义；不清则 30 天后
    `run_cleanup` 仍会选中这些 pending 行做空转（无害但多占扫描）并留下悬空审计。
    """
    if not content_ids:
        return
    db.execute(
        delete(SyncFieldVersion).where(SyncFieldVersion.entity_id.in_(content_ids))
    )
    db.execute(
        update(DeletedLog)
        .where(DeletedLog.content_id.in_(content_ids), DeletedLog.cleanup_status == "pending")
        .values(cleanup_status="done")
    )
