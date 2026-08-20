"""B4 数据同步服务（云端主库 · 字段级 LWW · 游标幂等）

设计（B4-2/3 已收敛）：
- 云端为主库，客户端只做"提交"，云端返回权威版本
- 字段级 LWW：每字段独立比较 updated_at 取最新（改标签不覆盖标题）
- 同字段同时间冲突 → 云端胜 + conflicts 提示（"另一台设备修改了此标签"）
- 软删除：deleted 墓碑同步各端，30 天物理清理（deleted_logs 对账）
- 幂等：客户端 op_id 全局唯一，offline_queue 去重（网络重试只执行一次）
- 增量拉取：offline_queue.id 全局单调 = 同步游标
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Content, DeletedLog, OfflineQueue, SyncFieldVersion, SyncState
from app.services.sync_common import TOMBSTONE_FIELD, lww_wins, parse_ts

logger = logging.getLogger("yishu.sync")

OP_UPSERT = "upsert_field"
OP_DELETE = "delete"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def push_ops(
    db: Session,
    user_id: str,
    device_id: str,
    ops: list[dict],
) -> dict:
    """客户端提交操作批次 → 字段级 LWW 应用，返回权威结果 + 冲突 + 拒绝

    ops: [{op_id, op_type: upsert_field|delete, entity_type, entity_id,
           field?, value?, updated_at?}]
    返回: {applied: [...], conflicts: [...], rejected: [...], server_version: int}

    安全（审查修复）：
    - 幂等检查按 (op_id, user_id) 双条件（防跨用户 op_id 碰撞静默跳过）
    - 归属校验：实体已存在且不属于当前用户 → 拒绝该条（rejected），不中断整批；
      entity_type=content 时额外对照 contents 表实体归属（防新建他人 UUID 实体）
    """
    applied: list[dict] = []
    conflicts: list[dict] = []
    rejected: list[dict] = []
    for op in ops:
        op_id = op.get("op_id")
        # 幂等：同用户 op_id 已存在 → 跳过（网络重试同一操作只执行一次）
        if op_id:
            existed = db.execute(
                select(OfflineQueue).where(
                    OfflineQueue.op_id == op_id,
                    OfflineQueue.user_id == user_id,
                )
            ).scalar_one_or_none()
            if existed is not None:
                continue

        entity_type = op.get("entity_type", "content")
        entity_id = op.get("entity_id")
        if not entity_id:
            continue
        client_ts = parse_ts(op.get("updated_at")) or _utcnow()
        op_type = op.get("op_type", OP_UPSERT)

        def _reject(reason: str, _op_id=op_id, _entity_id=entity_id) -> None:
            rejected.append(
                {"op_id": _op_id, "entity_id": _entity_id, "reason": reason}
            )

        # 归属校验：contents 实体存在且非本人 → 拒绝
        if entity_type == "content":
            content = db.get(Content, entity_id)
            if content is not None and str(content.user_id) != user_id:
                _reject("entity 不属于当前用户")
                continue

        if op_type == OP_DELETE:
            # 软删除墓碑：标记 deleted（entity 级），记录 deleted_logs
            row = db.execute(
                select(SyncFieldVersion).where(
                    SyncFieldVersion.entity_type == entity_type,
                    SyncFieldVersion.entity_id == entity_id,
                    SyncFieldVersion.field == TOMBSTONE_FIELD,
                )
            ).scalar_one_or_none()
            if row is not None and row.user_id != user_id:
                _reject("entity 不属于当前用户")
                continue
            # 墓碑不存在时：以实体任意字段行的归属为准（实体可能只有字段行）
            if row is None:
                owner = db.execute(
                    select(SyncFieldVersion.user_id)
                    .where(
                        SyncFieldVersion.entity_type == entity_type,
                        SyncFieldVersion.entity_id == entity_id,
                    )
                    .limit(1)
                ).scalar_one_or_none()
                if owner is not None and str(owner) != user_id:
                    _reject("entity 不属于当前用户")
                    continue
                if owner is None:
                    # 实体在云端无任何记录（无墓碑、无字段行、contents 亦无）→ 无可删实体
                    _reject("entity 不存在")
                    continue
            if row is None:
                row = SyncFieldVersion(
                    entity_type=entity_type, entity_id=entity_id, field=TOMBSTONE_FIELD, user_id=user_id
                )
                db.add(row)
            row.deleted = True
            row.updated_at = client_ts
            db.add(DeletedLog(content_id=entity_id, deleted_by=user_id))
            applied.append({"op_id": op_id, "entity_id": entity_id, "deleted": True})
        else:
            field = op.get("field")
            if not field:
                continue
            value = op.get("value")
            # 字段级 LWW：客户端时间 > 云端 → 更新；否则云端胜（冲突提示）
            row = db.execute(
                select(SyncFieldVersion).where(
                    SyncFieldVersion.entity_type == entity_type,
                    SyncFieldVersion.entity_id == entity_id,
                    SyncFieldVersion.field == field,
                )
            ).scalar_one_or_none()
            if row is not None and row.user_id != user_id:
                _reject("entity 不属于当前用户")
                continue
            if row is not None and not lww_wins(row.updated_at, client_ts):
                conflicts.append(
                    {
                        "op_id": op_id,
                        "entity_id": entity_id,
                        "field": field,
                        "server_value": row.value,
                        "hint": "另一台设备修改了此字段，已保留云端版本",
                    }
                )
            else:
                if row is None:
                    row = SyncFieldVersion(
                        entity_type=entity_type, entity_id=entity_id, field=field, user_id=user_id
                    )
                    db.add(row)
                row.value = value
                row.updated_at = client_ts
                row.deleted = False
                applied.append(
                    {"op_id": op_id, "entity_id": entity_id, "field": field, "value": value}
                )

        # 记录变更日志（增量拉取源 + 幂等键）
        if op_id:
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
                        "field": op.get("field"),
                        "value": op.get("value"),
                        "updated_at": client_ts.isoformat(),
                    },
                    status="done",
                )
            )

    db.commit()
    server_version = _max_version(db, user_id)
    return {"applied": applied, "conflicts": conflicts, "rejected": rejected, "server_version": server_version}


def push_ops_safe(db: Session, user_id: str, device_id: str, ops: list[dict]) -> dict:
    """push_ops 并发安全包装（审查修复 P1-04）

    并发同 op_id 重试 → 唯一约束冲突（uq_offline_queue_user_op）→ 回滚后
    重试一次（幂等检查会跳过已提交的 op）。仍失败则抛错（不吞）。
    """
    from sqlalchemy.exc import IntegrityError

    try:
        return push_ops(db, user_id, device_id, ops)
    except IntegrityError:
        db.rollback()
        return push_ops(db, user_id, device_id, ops)


def pull_changes(
    db: Session,
    user_id: str,
    device_id: str,
    since: int = 0,
    limit: int = 200,
) -> dict:
    """增量拉取：自游标以来的变更日志（offline_queue.id 单调）

    客户端按 op payload 重放 → 本地状态与云端一致（幂等）。
    返回 {changes: [...], cursor: int, has_more: bool}
    """
    rows = db.execute(
        select(OfflineQueue)
        .where(OfflineQueue.user_id == user_id, OfflineQueue.id > since)
        .order_by(OfflineQueue.id)
        .limit(limit + 1)
    ).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]
    changes = [
        {
            "version": r.id,
            "op_id": r.op_id,
            **r.payload,
        }
        for r in rows
    ]
    cursor = max([r.id for r in rows], default=since)
    # 记录该端游标
    state = db.execute(
        select(SyncState).where(
            SyncState.user_id == user_id, SyncState.device_id == device_id
        )
    ).scalar_one_or_none()
    if state is None:
        state = SyncState(user_id=user_id, device_id=device_id)
        db.add(state)
    state.cursor_version = cursor
    state.last_sync_at = _utcnow()
    db.commit()
    return {"changes": changes, "cursor": cursor, "has_more": has_more}


def _max_version(db: Session, user_id: str) -> int:
    """用户级同步游标（审查修复 P1-14）：取当前用户的最大 OfflineQueue.id

    原实现取全库最大 id——客户端以它作 pull since 会跨用户跳变/丢变更。
    """
    row = db.execute(
        select(OfflineQueue.id)
        .where(OfflineQueue.user_id == user_id)
        .order_by(OfflineQueue.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    return row or 0

