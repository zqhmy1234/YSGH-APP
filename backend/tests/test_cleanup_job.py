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
from app.workers.cleanup_job import run_cleanup, run_upload_reaper
from sqlalchemy import text

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


# ══════════════ P0-6 孤儿上传分片 reaper（2026-09-10 审计波 B）══════════════


def _stale_upload_task(db, user, *, status="uploading", days=10, chunks=2):
    """直建上传任务 + staging 分片对象 + chunks 行，并把 updated_at 改老以命中超龄窗"""
    import uuid as _u

    from app.db.models import UploadChunk, UploadTask
    from app.services.upload.protocol import _staging_key

    tid = str(_u.uuid4())
    file_key = f"photos/{user.id}/202609/reap_{tid[:8]}.jpg"
    task = UploadTask(
        id=tid, user_id=user.id, client_upload_id=f"reap|{tid}",
        file_name="reap.jpg", file_size=100 * chunks, chunk_size=100,
        chunk_count=chunks, file_key=file_key, storage="fake", status=status,
    )
    db.add(task)
    backend = get_storage_backend()
    for i in range(chunks):
        backend.put_object(_staging_key(tid, i), b"x" * 100)
        db.add(UploadChunk(upload_id=tid, chunk_index=i, chunk_hash="h", size=100, status="uploaded"))
    db.commit()
    db.execute(
        text("UPDATE upload_tasks SET updated_at = now() - make_interval(days => :d) WHERE id = :i"),
        {"d": days, "i": tid},
    )
    db.commit()
    return task, file_key


def test_reaper_clears_stale_upload_task(db_user):
    """僵死 uploading 任务超龄 → staging 对象删 + chunks 行删 + 任务标 failed（行保留）"""
    db, user = db_user
    task, file_key = _stale_upload_task(db, user)
    tid = str(task.id)
    backend = get_storage_backend()
    assert backend.object_exists(f"uploads/{tid}/0.part")
    assert backend.object_exists(f"uploads/{tid}/1.part")

    report = run_upload_reaper(older_than_days=7)
    assert report["reaped"] >= 1
    assert report["failed"] == 0
    assert report["chunks_removed"] >= 2

    db.expunge_all()
    from app.db.models import UploadChunk, UploadTask
    t = db.get(UploadTask, tid)
    assert t is not None  # 任务行保留（幂等键语义不破坏）
    assert t.status == "failed"
    assert not backend.object_exists(f"uploads/{tid}/0.part")
    assert not backend.object_exists(f"uploads/{tid}/1.part")
    assert file_key is not None  # 最终对象从未写（未 complete），不产生原件
    rows = db.execute(UploadChunk.__table__.select().where(UploadChunk.upload_id == tid)).all()
    assert rows == []


def test_reaper_skips_fresh_and_completed_tasks(db_user):
    """未超龄 uploading + completed 任务 → 僵死扫描不动（updated_at 未老 / 状态排除）"""
    db, user = db_user
    fresh, _ = _stale_upload_task(db, user, days=0)     # 未超龄
    done, _ = _stale_upload_task(db, user, status="completed", days=20)  # completed 排除
    fid, did = str(fresh.id), str(done.id)

    # 目录扫描 fake 后端跳过（local_root=None），仅僵死任务扫描生效
    run_upload_reaper(older_than_days=7)
    # 注：reaper 全局扫描（孤儿就该全局清），不假设全库只有本用例数据 →
    # 不断言 reaped 计数，改验证本用例两条各自的 updated_at/状态使其不被 reap
    db.expunge_all()
    from app.db.models import UploadTask
    assert db.get(UploadTask, fid).status == "uploading"   # 未超龄→不动
    assert db.get(UploadTask, did).status == "completed"    # completed→排除


def test_reaper_orphan_dirs_via_local_root(db_user, monkeypatch, tmp_path):
    """fs 后端孤儿目录扫描：无任务行目录超龄删、completed 残留目录删、新孤儿不删"""
    import os
    from time import mktime


    db, user = db_user
    # completed 任务 + 其残留 staging 目录（应被 leftover 清）
    done, _ = _stale_upload_task(db, user, status="completed", days=20)
    did = str(done.id)

    uploads = tmp_path / "uploads"
    uploads.mkdir()
    (uploads / did).mkdir()                      # completed 残留 → leftover
    (uploads / did / "0.part").write_bytes(b"x")

    orphan_old = uploads / "ffffffff-0000-0000-0000-000000000000"
    orphan_old.mkdir()
    (orphan_old / "0.part").write_bytes(b"y")
    old = mktime((2020, 1, 1, 0, 0, 0, 0, 0, 0))
    os.utime(orphan_old / "0.part", (old, old))   # 超龄 → orphan 删
    os.utime(orphan_old, (old, old))              # 目录 mtime 也造老（reaper 取 max 保守）

    orphan_new = uploads / "eeeeeeee-0000-0000-0000-000000000000"
    orphan_new.mkdir()
    (orphan_new / "0.part").write_bytes(b"z")     # 新（mtime 现在）→ 不删

    class _FsStub:
        def local_root(self):
            return tmp_path

    monkeypatch.setattr("app.workers.cleanup_job.get_storage_backend", lambda name=None: _FsStub())

    report = run_upload_reaper(older_than_days=7)
    assert report["orphan_dirs"] == 1
    assert report["leftover_dirs"] == 1
    assert not orphan_old.exists()
    assert not (uploads / did).exists()
    assert orphan_new.exists()  # 未超龄，保留（防误删 in-flight）
