"""B4 同步 API 测试（字段级 LWW · 游标幂等 · 软删除墓碑）

覆盖：
  - op_id 幂等（网络重试同一操作只执行一次）
  - 字段级 LWW：新时间覆盖 / 旧时间不覆盖（云端胜 + 冲突提示）
  - 同字段同时刻冲突 → 云端胜
  - 软删除墓碑同步
  - 增量拉取游标（since → 新变更）
  - API 冒烟
前置：PG yishu 库
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.db.models import DeletedLog, OfflineQueue, SyncFieldVersion, SyncState, User
from app.db.session import SessionLocal
from app.services.sync import pull_changes, push_ops
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

pytestmark = pytest.mark.integration

DEVICE = "test-device-1"


@pytest.fixture()
def db_user():
    db = SessionLocal()
    user = User(phone=f"sync-test-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    # 清理（顺序：先子表）
    for model in (OfflineQueue, SyncFieldVersion, SyncState, DeletedLog):
        db.execute(sa_delete(model).where(
            model.user_id == user.id
        )) if hasattr(model, "user_id") else None
    db.execute(
        sa_delete(SyncFieldVersion).where(
            SyncFieldVersion.entity_id.in_(
                [f"00000000-0000-0000-0000-{i:012d}" for i in range(1, 10)]
            )
        )
    )
    db.execute(sa_delete(OfflineQueue).where(OfflineQueue.user_id == user.id))
    db.execute(sa_delete(DeletedLog).where(DeletedLog.deleted_by == user.id))
    db.delete(user)
    db.commit()
    db.close()


def _eid(n: int) -> str:
    return f"00000000-0000-0000-0000-{n:012d}"


def _ts(days: int = 0, hours: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days, hours=hours)).isoformat()


def test_push_idempotent_by_op_id(db_user):
    """op_id 幂等：同一操作重发只应用一次"""
    db, user = db_user
    op = {
        "op_id": f"op-{uuid.uuid4().hex[:8]}",
        "op_type": "upsert_field",
        "entity_id": _eid(1),
        "field": "title",
        "value": "第一次",
        "updated_at": _ts(),
    }
    r1 = push_ops(db, user.id, DEVICE, [op])
    r2 = push_ops(db, user.id, DEVICE, [op])  # 重发
    assert len(r1["applied"]) == 1
    assert len(r2["applied"]) == 0, "重复 op_id 不应再次应用"
    assert r2["conflicts"] == []


def test_lww_newer_wins(db_user):
    """字段级 LWW：客户端时间更新 → 覆盖云端"""
    db, user = db_user
    eid = _eid(2)
    push_ops(db, user.id, DEVICE, [{
        "op_id": f"a-{uuid.uuid4().hex[:8]}", "op_type": "upsert_field",
        "entity_id": eid, "field": "tags", "value": ["旧标签"],
        "updated_at": _ts(days=-1),
    }])
    r = push_ops(db, user.id, DEVICE, [{
        "op_id": f"b-{uuid.uuid4().hex[:8]}", "op_type": "upsert_field",
        "entity_id": eid, "field": "tags", "value": ["新标签"],
        "updated_at": _ts(),
    }])
    assert len(r["applied"]) == 1
    row = db.execute(
        SyncFieldVersion.__table__.select().where(
            SyncFieldVersion.entity_id == eid, SyncFieldVersion.field == "tags"
        )
    ).first()
    assert row.value == ["新标签"]


def test_lww_older_loses_with_conflict(db_user):
    """字段级 LWW：客户端时间更旧 → 云端胜 + 冲突提示"""
    db, user = db_user
    eid = _eid(3)
    push_ops(db, user.id, DEVICE, [{
        "op_id": f"a-{uuid.uuid4().hex[:8]}", "op_type": "upsert_field",
        "entity_id": eid, "field": "place", "value": "云端地点",
        "updated_at": _ts(),
    }])
    r = push_ops(db, user.id, DEVICE, [{
        "op_id": f"b-{uuid.uuid4().hex[:8]}", "op_type": "upsert_field",
        "entity_id": eid, "field": "place", "value": "旧设备地点",
        "updated_at": _ts(days=-2),
    }])
    assert r["applied"] == []
    assert len(r["conflicts"]) == 1
    assert r["conflicts"][0]["server_value"] == "云端地点"
    assert "另一台设备" in r["conflicts"][0]["hint"]


def test_soft_delete_tombstone(db_user):
    """软删除：delete 操作 → 墓碑同步 + deleted_logs 记录"""
    db, user = db_user
    eid = _eid(4)
    push_ops(db, user.id, DEVICE, [{
        "op_id": f"c-{uuid.uuid4().hex[:8]}", "op_type": "upsert_field",
        "entity_id": eid, "field": "title", "value": "将被删除",
        "updated_at": _ts(),
    }])
    r = push_ops(db, user.id, DEVICE, [{
        "op_id": f"d-{uuid.uuid4().hex[:8]}", "op_type": "delete",
        "entity_id": eid, "updated_at": _ts(),
    }])
    assert r["applied"][0]["deleted"] is True
    row = db.execute(
        SyncFieldVersion.__table__.select().where(
            SyncFieldVersion.entity_id == eid, SyncFieldVersion.field == "*"
        )
    ).first()
    assert row.deleted is True
    logs = db.execute(DeletedLog.__table__.select().where(DeletedLog.content_id == eid)).first()
    assert logs is not None


def test_pull_incremental_cursor(db_user):
    """增量拉取：since 游标 → 只返回新变更"""
    db, user = db_user
    eid = _eid(5)
    push_ops(db, user.id, DEVICE, [{
        "op_id": f"p1-{uuid.uuid4().hex[:8]}", "op_type": "upsert_field",
        "entity_id": eid, "field": "title", "value": "v1", "updated_at": _ts(),
    }])
    first = pull_changes(db, user.id, DEVICE, since=0)
    assert len(first["changes"]) == 1
    assert first["cursor"] > 0

    push_ops(db, user.id, DEVICE, [{
        "op_id": f"p2-{uuid.uuid4().hex[:8]}", "op_type": "upsert_field",
        "entity_id": eid, "field": "title", "value": "v2", "updated_at": _ts(),
    }])
    second = pull_changes(db, user.id, DEVICE, since=first["cursor"])
    assert len(second["changes"]) == 1
    assert second["changes"][0]["value"] == "v2"
    assert second["cursor"] > first["cursor"]


def test_push_rejects_cross_user_entity(db_user):
    """安全修复：用户 B 不能 upsert/delete 用户 A 的实体（越权拒绝）"""
    db, user_a = db_user
    # 建第二个用户 B
    user_b = User(phone=f"sync-test-b-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user_b)
    db.commit()
    db.refresh(user_b)
    try:
        eid = _eid(7)
        # A 创建实体
        push_ops(db, user_a.id, DEVICE, [{
            "op_id": f"a-{uuid.uuid4().hex[:8]}", "op_type": "upsert_field",
            "entity_id": eid, "field": "title", "value": "A的标题", "updated_at": _ts(),
        }])
        # B 尝试改同一实体 → rejected，值不被覆盖
        r = push_ops(db, user_b.id, "test-device-b", [{
            "op_id": f"b-{uuid.uuid4().hex[:8]}", "op_type": "upsert_field",
            "entity_id": eid, "field": "title", "value": "B篡改", "updated_at": _ts(),
        }])
        assert len(r["rejected"]) == 1, r
        assert r["applied"] == []
        row = db.execute(
            SyncFieldVersion.__table__.select().where(
                SyncFieldVersion.entity_id == eid, SyncFieldVersion.field == "title"
            )
        ).first()
        assert row.value == "A的标题"
        # B 尝试 delete → rejected
        r2 = push_ops(db, user_b.id, "test-device-b", [{
            "op_id": f"b2-{uuid.uuid4().hex[:8]}", "op_type": "delete",
            "entity_id": eid, "updated_at": _ts(),
        }])
        assert len(r2["rejected"]) == 1
        assert r2["applied"] == []
    finally:
        # 清理 B 的子表记录（FK 顺序：先子后父）
        db.execute(sa_delete(OfflineQueue).where(OfflineQueue.user_id == user_b.id))
        db.execute(sa_delete(DeletedLog).where(DeletedLog.deleted_by == user_b.id))
        db.delete(user_b)
        db.commit()


def test_op_id_idempotent_scoped_per_user(db_user):
    """安全修复：op_id 幂等按用户隔离（用户 B 用同 op_id 不被他 A 跳过）"""
    db, user_a = db_user
    user_b = User(phone=f"sync-test-c-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user_b)
    db.commit()
    db.refresh(user_b)
    try:
        shared_op_id = f"shared-{uuid.uuid4().hex[:8]}"
        r1 = push_ops(db, user_a.id, DEVICE, [{
            "op_id": shared_op_id, "op_type": "upsert_field",
            "entity_id": _eid(8), "field": "title", "value": "A", "updated_at": _ts(),
        }])
        assert len(r1["applied"]) == 1
        # B 用相同 op_id（全局唯一性由客户端保证，但服务端不能跨用户拦截）
        r2 = push_ops(db, user_b.id, "test-device-b", [{
            "op_id": shared_op_id, "op_type": "upsert_field",
            "entity_id": _eid(9), "field": "title", "value": "B", "updated_at": _ts(),
        }])
        assert len(r2["applied"]) == 1, "跨用户 op_id 不应幂等跳过"
    finally:
        # 清理 user_b（2026-08-26：补 SyncFieldVersion——push_ops 会写 sync_field_versions，
        # 完整 FK schema 下删 user 被 sync_field_versions_user_id_fkey 拦，本地旧库无 FK 掩盖）
        db.execute(sa_delete(SyncFieldVersion).where(SyncFieldVersion.user_id == user_b.id))
        db.execute(sa_delete(OfflineQueue).where(OfflineQueue.user_id == user_b.id))
        db.execute(sa_delete(DeletedLog).where(DeletedLog.deleted_by == user_b.id))
        db.delete(user_b)
        db.commit()


def test_push_same_key_in_batch_no_500(db_user):
    """R2#6：批内同键重复 op（同 entity+field 多条）→ 不再 IntegrityError

    同批两条 op 写同一 (entity_type, entity_id, field)：首条新建行立即登记回映射，
    第二条命中刚建的行按 LWW 合并——终值 = 较新 op，整批提交成功。
    """
    db, user = db_user
    eid = _eid(10)
    r = push_ops(db, user.id, DEVICE, [
        {"op_id": f"k1-{uuid.uuid4().hex[:8]}", "op_type": "upsert_field",
         "entity_id": eid, "field": "title", "value": "先", "updated_at": _ts()},
        {"op_id": f"k2-{uuid.uuid4().hex[:8]}", "op_type": "upsert_field",
         "entity_id": eid, "field": "title", "value": "后", "updated_at": _ts(hours=1)},
    ])
    assert len(r["applied"]) == 2, r
    row = db.execute(
        select(SyncFieldVersion).where(
            SyncFieldVersion.entity_id == eid, SyncFieldVersion.field == "title"
        )
    ).scalar_one()
    assert row.value == "后", "LWW：较新的 op 胜出"


def test_push_delete_delete_in_batch(db_user):
    """R2#6：批内同实体两条 delete（实体已存在）→ 墓碑行登记回映射，不再 IntegrityError"""
    db, user = db_user
    eid = _eid(11)
    r = push_ops(db, user.id, DEVICE, [
        {"op_id": f"u1-{uuid.uuid4().hex[:8]}", "op_type": "upsert_field",
         "entity_id": eid, "field": "title", "value": "先有实体", "updated_at": _ts()},
        {"op_id": f"d1-{uuid.uuid4().hex[:8]}", "op_type": "delete",
         "entity_id": eid, "updated_at": _ts(hours=1)},
        {"op_id": f"d2-{uuid.uuid4().hex[:8]}", "op_type": "delete",
         "entity_id": eid, "updated_at": _ts(hours=2)},
    ])
    assert len(r["applied"]) == 3, r
    rows = db.execute(
        select(SyncFieldVersion).where(
            SyncFieldVersion.entity_id == eid, SyncFieldVersion.field == "*"
        )
    ).scalars().all()
    assert len(rows) == 1, "同实体墓碑只应有一行"


def test_push_ops_safe_per_op_fallback(db_user, monkeypatch):
    """R2#6：整批两次重试仍 IntegrityError → 逐条隔离，冲突单条 rejected 不拖垮整批"""
    import app.services.sync as sync_mod
    from sqlalchemy.exc import IntegrityError

    db, user = db_user
    bad_op = {"op_id": "conflict-op", "op_type": "upsert_field",
              "entity_id": _eid(12), "field": "title", "value": "冲突", "updated_at": _ts()}
    good_op = {"op_id": f"good-{uuid.uuid4().hex[:8]}", "op_type": "upsert_field",
               "entity_id": _eid(13), "field": "title", "value": "正常", "updated_at": _ts()}

    real = sync_mod.push_ops

    def fake_push(dbs, uid, did, ops):
        if ops and ops[0].get("op_id") == "conflict-op":
            raise IntegrityError("mock conflict", {}, Exception("dup"))
        return real(dbs, uid, did, ops)

    monkeypatch.setattr(sync_mod, "push_ops", fake_push)
    result = sync_mod._push_ops_per_op(db, user.id, DEVICE, [bad_op, good_op])
    assert result["rejected"] == [{"op_id": "conflict-op", "entity_id": _eid(12),
                                   "reason": "并发冲突（op 幂等键/字段行无法落库）"}]
    assert [a["op_id"] for a in result["applied"]] == [good_op["op_id"]], "正常 op 照常应用"


def test_sync_api_smoke(db_user):
    """API 冒烟：push + pull"""
    from app.api import deps
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    client = TestClient(app)

    def fake_user():
        return user

    app.dependency_overrides[deps.get_current_user] = fake_user
    try:
        eid = _eid(6)
        r = client.post("/api/v1/sync/push", json={
            "device_id": DEVICE,
            "ops": [{
                "op_id": f"api-{uuid.uuid4().hex[:8]}", "op_type": "upsert_field",
                "entity_id": eid, "field": "title", "value": "API标题",
                "updated_at": _ts(),
            }],
        })
        assert r.status_code == 200, r.text
        assert r.json()["data"]["applied"]

        r2 = client.get(f"/api/v1/sync/pull?device_id={DEVICE}&since=0")
        assert r2.status_code == 200, r2.text
        assert r2.json()["data"]["changes"]
    finally:
        app.dependency_overrides.clear()
