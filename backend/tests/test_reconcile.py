"""端云对账测试（S5-04 · WP-D）

覆盖：
  - 空对账：双方为空 → 全零
  - missing_on_cloud：客户端有、云端无
  - missing_on_client：云端有、客户端无
  - divergent：时间不同 → LWW 判定 newer/action
  - 软删除墓碑参与对账（deleted 状态不一致 → divergent）
前置：PG yishu 库
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.db.models import DeletedLog, OfflineQueue, SyncFieldVersion, User
from app.db.session import SessionLocal
from app.services.reconcile import reconcile_snapshot
from app.services.sync import push_ops
from sqlalchemy import delete as sa_delete

pytestmark = pytest.mark.integration


@pytest.fixture()
def db_user():
    db = SessionLocal()
    user = User(phone=f"recon-test-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    db.execute(sa_delete(OfflineQueue).where(OfflineQueue.user_id == user.id))
    db.execute(sa_delete(DeletedLog).where(DeletedLog.deleted_by == user.id))
    db.execute(sa_delete(SyncFieldVersion).where(SyncFieldVersion.user_id == user.id))
    db.delete(user)
    db.commit()
    db.close()


def _eid() -> str:
    # 随机 UUID：避免与库内真实 content / 历史测试残留撞车
    return str(uuid.uuid4())


def _ts(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def test_empty_reconcile(db_user):
    db, user = db_user
    report = reconcile_snapshot(db, user.id, [])
    assert report["summary"] == {"cloud_entities": 0, "client_entities": 0, "need_push": 0, "need_pull": 0}


def test_missing_on_cloud(db_user):
    db, user = db_user
    eid = _eid()
    report = reconcile_snapshot(db, user.id, [{"entity_id": eid, "updated_at": _ts(0)}])
    assert len(report["missing_on_cloud"]) == 1
    assert report["missing_on_cloud"][0]["entity_id"] == eid
    assert report["summary"]["need_push"] == 1


def test_missing_on_client(db_user):
    db, user = db_user
    eid = _eid()
    result = push_ops(db, user.id, "d1", [
        {"op_id": "r1", "op_type": "upsert_field", "entity_type": "content",
         "entity_id": eid, "field": "title", "value": "云端记录", "updated_at": _ts(0)},
    ])
    assert result["applied"], f"push 未生效: {result}"
    report = reconcile_snapshot(db, user.id, [])
    assert report["summary"]["cloud_entities"] == 1, f"云端应 1 实体: {report['summary']}"
    assert len(report["missing_on_client"]) == 1


def test_divergent_lww_client_newer(db_user):
    db, user = db_user
    eid = _eid()
    push_ops(db, user.id, "d1", [
        {"op_id": "r2", "op_type": "upsert_field", "entity_type": "content",
         "entity_id": eid, "field": "title", "value": "旧", "updated_at": _ts(-2)},
    ])
    report = reconcile_snapshot(db, user.id, [{"entity_id": eid, "updated_at": _ts(1)}])
    assert len(report["divergent"]) == 1
    assert report["divergent"][0]["newer"] == "client"
    assert report["divergent"][0]["action"] == "push"


def test_divergent_lww_cloud_newer(db_user):
    db, user = db_user
    eid = _eid()
    push_ops(db, user.id, "d1", [
        {"op_id": "r3", "op_type": "upsert_field", "entity_type": "content",
         "entity_id": eid, "field": "title", "value": "新", "updated_at": _ts(2)},
    ])
    report = reconcile_snapshot(db, user.id, [{"entity_id": eid, "updated_at": _ts(-1)}])
    assert len(report["divergent"]) == 1
    assert report["divergent"][0]["newer"] == "cloud"
    assert report["divergent"][0]["action"] == "pull"


def test_tombstone_divergent(db_user):
    db, user = db_user
    eid = _eid()
    # 先建实体，再软删（云端无实体时 delete 会被拒 "entity 不存在"）
    push_ops(db, user.id, "d1", [
        {"op_id": "r4a", "op_type": "upsert_field", "entity_type": "content",
         "entity_id": eid, "field": "title", "value": "x", "updated_at": _ts(-1)},
    ])
    push_ops(db, user.id, "d1", [
        {"op_id": "r4", "op_type": "delete", "entity_type": "content",
         "entity_id": eid, "updated_at": _ts(0)},
    ])
    # 客户端仍持有该实体且未删 → deleted 状态不一致 → divergent
    report = reconcile_snapshot(db, user.id, [{"entity_id": eid, "updated_at": _ts(0), "deleted": False}])
    assert len(report["divergent"]) == 1
