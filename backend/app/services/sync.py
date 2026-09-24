"""B4 数据同步服务（云端主库 · 字段级 LWW · 游标幂等）

设计（B4-2/3 已收敛）：
- 云端为主库，客户端只做"提交"，云端返回权威版本
- 字段级 LWW：每字段独立比较 updated_at 取最新（改标签不覆盖标题）
- 同字段同时间冲突 → 云端胜 + conflicts 提示（"另一台设备修改了此标签"）
- 软删除：deleted 墓碑同步各端 + **投影权威表 `contents.deleted_at`** + 幂等
  `deleted_logs`（⇒ 立即隐藏 / 回收站可恢复 / 30 天彻底清除 三者同时成立）。
  与 REST `DELETE` **同调** `services/sync_writes`（单一软删语义路径，D08-1/2）。
- 字段级写入：LWW 胜出后**白名单字段投影回权威表**（D08-3，content 现仅 `remark`）；
  白名单外字段对存在权威行的实体**显式拒绝**（不静默记账假装成功）；
  冲突/拒绝**不写**变更日志（D08-5）。
- 增量拉取：offline_queue.id 全局单调 = 同步游标
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from app.db.models import Content, Event, OfflineQueue, SyncFieldVersion, SyncState
from app.services import sync_writes
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

    S6-3 性能修复：op_id 幂等查 / content 归属查 / SyncFieldVersion 查按批 IN 预取
    （N+1 → 每批 3 次查询），逐 op 冲突判定语义保留不变。
    """
    applied: list[dict] = []
    conflicts: list[dict] = []
    rejected: list[dict] = []

    # ── S6-3 批量预取 ──
    # ① 幂等：本用户已存在的 op_id 集合（一次查）
    truthy_op_ids = [op.get("op_id") for op in ops if op.get("op_id")]
    existing_op_ids: set[str] = set()
    if truthy_op_ids:
        existing_op_ids = set(
            db.execute(
                select(OfflineQueue.op_id).where(
                    OfflineQueue.user_id == user_id,
                    OfflineQueue.op_id.in_(truthy_op_ids),
                )
            ).scalars().all()
        )

    # ② content 实体归属 + 权威行（entity_type=content 一次查；无 deleted 过滤，与原
    #     db.get 一致）。**载入 ORM 行而非仅 (id, user_id)**：软删投影（D08-1：置
    #     contents.deleted_at）与字段投影（D08-3：写回 contents.remark）都要落到权威行，
    #     复用同一次查询，避免逐 op N+1。
    content_ids = [
        str(op["entity_id"]) for op in ops
        if op.get("entity_type") == "content" and op.get("entity_id")
    ]
    content_by_id: dict[str, Content] = {}
    if content_ids:
        for row in db.execute(
            select(Content).where(Content.id.in_(content_ids))
        ).scalars().all():
            content_by_id[str(row.id)] = row
    content_owner: dict[str, str] = {
        cid: str(row.user_id) for cid, row in content_by_id.items()
    }

    # ②b event 实体归属（P2-1 · 2026-09-10 深扫修复）：entity_type=event 必须对照 events
    #     权威表校验 owner——原实现只校验 content，event/profile 首次写入（云端无 SFV 行）
    #     直接以 attacker user_id 建行 → 受害者反被 sfv 归属校验永久拒绝（投毒/完整性破坏）。
    event_ids = [
        str(op["entity_id"]) for op in ops
        if op.get("entity_type") == "event" and op.get("entity_id")
    ]
    event_owner: dict[str, str] = {}
    if event_ids:
        for eid, uid in db.execute(
            select(Event.id, Event.user_id).where(Event.id.in_(event_ids))
        ).all():
            event_owner[str(eid)] = str(uid)

    # ③ SyncFieldVersion 按 (entity_type, entity_id) 组合批量预取：
    #    含墓碑行（TOMBSTONE_FIELD）与逐字段行 + 实体归属（任意字段行的 user_id）
    combos = {
        (op.get("entity_type", "content"), str(op["entity_id"]))
        for op in ops if op.get("entity_id")
    }
    sfv_by_key: dict[tuple[str, str, str], SyncFieldVersion] = {}
    sfv_by_entity: dict[tuple[str, str], list[SyncFieldVersion]] = defaultdict(list)
    if combos:
        rows = db.execute(
            select(SyncFieldVersion).where(
                tuple_(SyncFieldVersion.entity_type, SyncFieldVersion.entity_id).in_(list(combos))
            )
        ).scalars().all()
        for r in rows:
            sfv_by_key[(r.entity_type, str(r.entity_id), r.field)] = r
            sfv_by_entity[(r.entity_type, str(r.entity_id))].append(r)

    for op in ops:
        op_id = op.get("op_id")
        # 幂等：同用户 op_id 已存在 → 跳过（网络重试同一操作只执行一次）
        if op_id in existing_op_ids:
            continue

        entity_type = op.get("entity_type", "content")
        entity_id = op.get("entity_id")
        if not entity_id:
            continue
        entity_id = str(entity_id)
        client_ts = parse_ts(op.get("updated_at")) or _utcnow()
        op_type = op.get("op_type", OP_UPSERT)

        def _reject(reason: str, _op_id=op_id, _entity_id=entity_id) -> None:
            rejected.append(
                {"op_id": _op_id, "entity_id": _entity_id, "reason": reason}
            )

        # 归属校验：contents 实体存在且非本人 → 拒绝
        if entity_type == "content":
            owner = content_owner.get(entity_id)
            if owner is not None and owner != str(user_id):
                _reject("entity 不属于当前用户")
                continue
        # P2-1：event 实体对照 events 权威表（存在且非本人 → 拒；不存在也拒防凭空投毒）；
        # profile 实体主键即 user_id → entity_id 必须等于本人，否则拒绝（防代写他人画像）
        elif entity_type == "event":
            owner = event_owner.get(entity_id)
            if owner is not None and owner != str(user_id):
                # P2-1（2026-09-10 深扫）：事件权威表存在且非本人 → 拒（首触投毒封堵）；
                # 不存在时维持原语义放行（端侧 L1 事件 sync-first 上云流，owner 由
                # client_event_id/事件创建路径补，见 events sync 域；不收紧防回归）
                _reject("entity 不属于当前用户")
                continue
        elif entity_type == "profile":
            if entity_id != str(user_id):
                _reject("profile entity_id 必须为当前用户")
                continue

        if op_type == OP_DELETE:
            # 软删除墓碑：标记 deleted（entity 级），记录 deleted_logs
            row = sfv_by_key.get((entity_type, entity_id, TOMBSTONE_FIELD))
            if row is not None and row.user_id != user_id:
                _reject("entity 不属于当前用户")
                continue
            # 墓碑不存在时：以实体任意字段行的归属为准（实体可能只有字段行）
            if row is None:
                entity_rows = sfv_by_entity.get((entity_type, entity_id))
                if entity_rows:
                    owner = entity_rows[0].user_id
                    if str(owner) != str(user_id):
                        _reject("entity 不属于当前用户")
                        continue
                elif entity_type == "content" and entity_id in content_by_id:
                    # D08-1 更深一层：**权威行存在即实体存在**。照片经 REST/分片链路
                    # 上云（photo_content / upload register）时**从不写 SFV 行**，原实现
                    # 只认 SFV ⇒ 这类内容的离线删除被误判"entity 不存在"而静默丢弃
                    # （客户端只看到 rejected，用户以为删了、云端其实没删）。归属已由
                    # 上文 content_owner 校验过，故此处放行并正常建墓碑。
                    pass
                else:
                    # 实体在云端无任何记录（无墓碑、无字段行、contents 亦无）→ 无可删实体
                    _reject("entity 不存在")
                    continue
            if row is None:
                row = SyncFieldVersion(
                    entity_type=entity_type, entity_id=entity_id, field=TOMBSTONE_FIELD, user_id=user_id
                )
                db.add(row)
                # R2#6：批内新行立即登记回映射——后续同实体 op 直接命中刚建的行，
                # 防同键双插 PK 冲突（sfv_by_key 只含批前预取行 → 批内同键重复 op 500）
                sfv_by_key[(entity_type, entity_id, TOMBSTONE_FIELD)] = row
                sfv_by_entity[(entity_type, entity_id)].append(row)
            row.deleted = True
            row.updated_at = client_ts
            # D08-1/2：软删的**唯一语义路径**——投影 contents.deleted_at（否则 REST
            # 读仍视为存活、回收站也不可见）+ 幂等 deleted_logs（否则 30 天清理永不
            # 选中）+ 变更日志。墓碑行本身已按批内映射写好，此处不重复写 SFV。
            sync_writes.finalize_soft_delete(
                db,
                user_id,
                entity_type=entity_type,
                entity_id=entity_id,
                change_ts=client_ts,
                content=content_by_id.get(entity_id) if entity_type == "content" else None,
                device_id=device_id,
                op_id=op_id,
            )
            applied.append({"op_id": op_id, "entity_id": entity_id, "deleted": True})
        else:
            field = op.get("field")
            if not field:
                continue
            content_row = content_by_id.get(entity_id) if entity_type == "content" else None
            # D08-3：对**存在权威行**的 content，白名单外字段显式拒绝——否则 SFV 记账
            # 成功（applied+1）却永不投影，用户看不到 = 谎（原缺陷正是如此）。
            # 无权威行的实体（历史/合成/未来类型）沿用通用 SFV 账本语义，本波不收窄。
            if content_row is not None and not sync_writes.is_syncable_field(entity_type, field):
                allowed = "/".join(sorted(sync_writes.CONTENT_SYNC_FIELDS))
                _reject(f"字段不可同步（content 仅支持 {allowed}）")
                continue
            value = op.get("value")
            if content_row is not None and not sync_writes.is_field_value_ok(
                entity_type, field, value
            ):
                _reject("字段值类型不符")
                continue
            # 字段级 LWW：客户端时间 > 云端 → 更新；否则云端胜（冲突提示）
            row = sfv_by_key.get((entity_type, entity_id, field))
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
                # D08-5：冲突即败方 —— **不写变更日志**。原实现无 continue，控制流穿透到
                # 日志写入，把被判败的客户端旧值原样下发，污染他端 replay。云端权威值在
                # 它自己那次胜出写入时已入日志，无需在此补写。
            else:
                if row is None:
                    row = SyncFieldVersion(
                        entity_type=entity_type, entity_id=entity_id, field=field, user_id=user_id
                    )
                    db.add(row)
                    # R2#6：批内新行立即登记回映射（同上，防同键双插 PK 冲突）
                    sfv_by_key[(entity_type, entity_id, field)] = row
                    sfv_by_entity[(entity_type, entity_id)].append(row)
                row.value = value
                row.updated_at = client_ts
                row.deleted = False
                # D08-3：白名单字段投影回权威表，与 SFV 决策**同生共死**——只有 LWW
                # 胜出（applied）才投影，杜绝"账本已改、权威未改"的分叉。
                if content_row is not None:
                    sync_writes.project_field(entity_type, content_row, field, value)
                applied.append(
                    {"op_id": op_id, "entity_id": entity_id, "field": field, "value": value}
                )
                # 变更日志（增量拉取源 + 幂等键）：只在真正应用后写（含 delete 分支
                # 由 finalize_soft_delete 写入；冲突/拒绝一律不写）。
                sync_writes.log_change(
                    db,
                    user_id,
                    device_id,
                    op_type=op_type,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    updated_at=client_ts,
                    field=op.get("field"),
                    value=op.get("value"),
                    op_id=op_id,
                )

    db.commit()
    server_version = _max_version(db, user_id)
    return {"applied": applied, "conflicts": conflicts, "rejected": rejected, "server_version": server_version}


def push_ops_safe(db: Session, user_id: str, device_id: str, ops: list[dict]) -> dict:
    """push_ops 并发安全包装（审查修复 P1-04 + R2#6 增强）

    并发同 op_id 重试 → 唯一约束冲突（uq_offline_queue_user_op）→ 回滚后
    重试一次（幂等检查会跳过已提交的 op）。仍失败 → 定位冲突 op 逐条隔离
    应用：冲突单条降级 rejected（不再整批 500），其余照常落库。
    """
    from sqlalchemy.exc import IntegrityError

    try:
        return push_ops(db, user_id, device_id, ops)
    except IntegrityError:
        db.rollback()
        try:
            return push_ops(db, user_id, device_id, ops)
        except IntegrityError:
            db.rollback()
            return _push_ops_per_op(db, user_id, device_id, ops)


def _push_ops_per_op(db: Session, user_id: str, device_id: str, ops: list[dict]) -> dict:
    """整批两次失败后的逐条隔离兜底（R2#6）：冲突单条 rejected，其余照常应用。

    每次单条 push_ops 独立 commit：失败条目回滚并记 rejected，不拖垮整批；
    applied/conflicts/rejected 语义与整批一致，客户端可观测定位到具体 op。
    """
    from sqlalchemy.exc import IntegrityError

    merged: dict = {"applied": [], "conflicts": [], "rejected": [], "server_version": 0}
    for op in ops:
        try:
            result = push_ops(db, user_id, device_id, [op])
        except IntegrityError:
            db.rollback()
            merged["rejected"].append(
                {
                    "op_id": op.get("op_id"),
                    "entity_id": str(op.get("entity_id") or ""),
                    "reason": "并发冲突（op 幂等键/字段行无法落库）",
                }
            )
            continue
        merged["applied"].extend(result["applied"])
        merged["conflicts"].extend(result["conflicts"])
        merged["rejected"].extend(result["rejected"])
        merged["server_version"] = max(merged["server_version"], result["server_version"])
    return merged


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

