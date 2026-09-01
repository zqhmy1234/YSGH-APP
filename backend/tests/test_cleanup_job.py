"""30 天物理清理 job 测试（B4 · Wave3 AgentG · audit #2 缺口修复）

覆盖：
  - 到期（≥30 天）pending 墓碑：物理删 COS 对象 + 删 contents 行 + 清 sync_field_versions 墓碑
    + cleanup_status → done
  - 未到期墓碑：不清理（skipped_not_due 计数）
  - done 状态墓碑：不重复处理（幂等）
  - dry_run：只扫描不删
前置：PG yishu 库（fake 存储由 conftest autouse 强制）
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.db.models import Content, DeletedLog, SyncFieldVersion
from app.services.external.storage import get_storage_backend
from app.workers.cleanup_job import run_cleanup

pytestmark = pytest.mark.integration


def _due_tombstone(db, user, days_ago: int = 40):
    """构造到期墓碑 + 对应 Content（含 COS 对象）+ 墓碑行"""
    content_id = str(uuid.uuid4())
    cos_key = f"photos/{user.id}/202607/cleanup_{uuid.uuid4().hex[:8]}.jpg"
    thumb_key = f"thumbnails/{user.id}/202607/cleanup_{uuid.uuid4().hex[:8]}.jpg"
    backend = get_storage_backend()
    backend.put_object(cos_key, b"original-bytes")
    backend.put_object(thumb_key, b"thumb-bytes")

    content = Content(
        id=content_id,
        user_id=user.id,
        content_type="photo",
        cos_key=cos_key,
        thumbnail_key=thumb_key,
        source="app",
        status="done",
        deleted_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
    )
    db.add(content)
    db.flush()  # 先拿 id
    # 墓碑行（entity 级 "*" + 字段行）
    for field in ("*", "title"):
        db.add(
            SyncFieldVersion(
                entity_type="content",
                entity_id=str(content.id),
                field=field,
                user_id=user.id,
                value={"title": "x"} if field == "title" else None,
                deleted=field == "*",
                updated_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
            )
        )
    log = DeletedLog(
        content_id=str(content.id),
        deleted_by=user.id,
        deleted_at=datetime.now(timezone.utc) - timedelta(days=days_ago),
        cleanup_status="pending",
    )
    db.add(log)
    db.commit()
    db.refresh(content)
    return content, log


def test_cleanup_due_tombstone_physically_removes(db_user):
    db, user = db_user
    content, log = _due_tombstone(db, user)
    backend = get_storage_backend()
    assert backend.object_exists(content.cos_key)
    assert backend.object_exists(content.thumbnail_key)

    report = run_cleanup(older_than_days=30)
    assert report["scanned"] >= 1
    assert report["failed"] == 0
    assert report["cleaned"] >= 1

    db.expunge_all()  # 清身份图：删除的 Content 实例不再残留，重新查询
    # COS 对象已物理删
    assert not backend.object_exists(content.cos_key)
    assert not backend.object_exists(content.thumbnail_key)
    # contents 行已删
    assert db.get(Content, str(content.id)) is None
    # 墓碑已清（sync_field_versions 该 entity 无行）
    rows = db.execute(
        SyncFieldVersion.__table__.select().where(SyncFieldVersion.entity_id == str(content.id))
    ).all()
    assert rows == []
    # 墓碑标记 done（审计保留）
    refreshed = db.get(DeletedLog, log.id)
    assert refreshed is not None
    assert refreshed.cleanup_status == "done"


def test_cleanup_skips_not_due_and_done(db_user):
    db, user = db_user
    # 未到期（仅 5 天前）
    recent, recent_log = _due_tombstone(db, user, days_ago=5)
    # 到期但已 done
    done, done_log = _due_tombstone(db, user, days_ago=40)
    done_log.cleanup_status = "done"
    db.commit()

    report = run_cleanup(older_than_days=30)
    db.expire_all()
    # 到期 pending 只有 done_log 的兄弟？（done_log 已 done → 不算）→ scanned=0
    assert report["scanned"] == 0
    assert report["cleaned"] == 0
    assert report["skipped_not_due"] >= 1
    # 未到期对象仍保留
    assert get_storage_backend().object_exists(recent.cos_key)
    assert db.get(Content, str(recent.id)) is not None
    # done 的墓碑对象仍保留（不重复删）
    assert get_storage_backend().object_exists(done.cos_key)
    assert db.get(DeletedLog, done_log.id).cleanup_status == "done"


def test_cleanup_dry_run_changes_nothing(db_user):
    db, user = db_user
    content, log = _due_tombstone(db, user)
    report = run_cleanup(older_than_days=30, dry_run=True)
    assert report["scanned"] >= 1
    assert report["cleaned"] >= 1  # dry_run 计数但不物理删
    db.expire_all()
    assert db.get(Content, str(content.id)) is not None
    assert get_storage_backend().object_exists(content.cos_key)
    assert db.get(DeletedLog, log.id).cleanup_status == "pending"


def test_cleanup_idempotent(db_user):
    db, user = db_user
    content, log = _due_tombstone(db, user)
    r1 = run_cleanup(older_than_days=30)
    assert r1["cleaned"] >= 1
    # 二次运行：无到期 pending 剩余 → 不再清理
    r2 = run_cleanup(older_than_days=30)
    assert r2["scanned"] == 0
    assert r2["cleaned"] == 0
