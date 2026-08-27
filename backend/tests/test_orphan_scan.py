"""孤儿对象扫描 job 测试（B1 · Wave1 · P0-6 缺口补口 · 收尾 §5.2.6）

覆盖：
  - 三类孤儿（staging 分片 / 未注册 cos_key / wechat 拦截对象）超龄 + 零引用 → 删
  - 被引用对象（contents.cos_key / thumbnail_key）→ 不删（含软删行）
  - 未超龄对象 → 不删
  - 活跃上传（upload_task 未完成且年轻）staging → 保留可续传
  - 已放弃上传（upload_task 未完成且超龄）staging → 删
  - 已完成任务分片残留 → 超龄删
  - dry_run：只报告不删
  - 幂等：二次运行 scanned_orphans 归零
  - 单对象删除失败 → 记 failed 不中断其余
  - limit 截断：scanned_orphans 全量，deleted ≤ limit
  - 后端无 list_objects（B3 未合入）→ skipped 报告不崩
前置：PG yishu 库；存储注入 FakeListableBackend（模拟 B3 list_objects 契约）
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.db.models import Content, UploadTask
from app.workers.orphan_scan import run_orphan_scan

pytestmark = pytest.mark.integration

NOW = datetime.now(timezone.utc)


class FakeListableBackend:
    """带 list_objects(prefix) -> [(key, last_modified)] + mtime 的内存后端

    模拟 B3 契约（18 号 C7）：list_objects 按前缀返回 (key, last_modified) 元组；
    delete_object 支持注入失败（fail_delete 集合）验证失败安全。
    """

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self._mtime: dict[str, datetime] = {}
        self._fail_delete: set[str] = set()

    def put_object(self, key: str, data: bytes) -> None:
        self._store[key] = data
        self._mtime.setdefault(key, NOW)

    def get_object(self, key: str) -> bytes:
        if key not in self._store:
            raise KeyError(key)
        return self._store[key]

    def delete_object(self, key: str) -> None:
        if key in self._fail_delete:
            raise RuntimeError(f"injected delete failure: {key}")
        self._store.pop(key, None)
        self._mtime.pop(key, None)

    def object_exists(self, key: str) -> bool:
        return key in self._store

    def list_objects(self, prefix: str) -> list[tuple[str, datetime | None]]:
        return [(k, t) for k, t in sorted(self._mtime.items()) if k.startswith(prefix)]

    def seed(self, key: str, age_days: int) -> None:
        """写对象并设 last_modified = NOW - age_days 天（构造超龄/未超龄）"""
        self.put_object(key, b"seed")
        self._mtime[key] = NOW - timedelta(days=age_days)


@pytest.fixture()
def listable_backend():
    return FakeListableBackend()


def _add_content(db, user, cos_key=None, thumb_key=None, deleted=False):
    content = Content(
        id=str(uuid.uuid4()),
        user_id=user.id,
        content_type="photo",
        cos_key=cos_key,
        thumbnail_key=thumb_key,
        source="app",
        status="done",
        deleted_at=NOW - timedelta(days=40) if deleted else None,
    )
    db.add(content)
    db.commit()
    db.refresh(content)
    return content


def _add_upload_task(db, user, upload_id, status, days_ago):
    task = UploadTask(
        id=upload_id,
        user_id=user.id,
        client_upload_id=f"cli-{upload_id[:8]}",
        file_name="a.jpg",
        file_size=1000,
        chunk_size=500,
        chunk_count=2,
        file_key=f"photos/{user.id}/202607/a.jpg",
        storage="fake",
        status=status,
        created_at=NOW - timedelta(days=days_ago),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def test_orphan_scan_deletes_three_categories_and_keeps_referenced(db_user, listable_backend):
    db, user = db_user
    # 三类孤儿（超龄 + 零引用）
    listable_backend.seed(f"uploads/{uuid.uuid4()}/0.part", age_days=40)
    listable_backend.seed(f"photos/{user.id}/202606/orphan.jpg", age_days=40)
    listable_backend.seed(f"wechat/{user.id}/msg123.jpg", age_days=40)
    # 被引用对象（contents.cos_key / thumbnail_key，超龄也保留）
    ref_key = f"photos/{user.id}/202606/ref.jpg"
    ref_thumb = f"thumbnails/{user.id}/202606/ref_t.jpg"
    _add_content(db, user, cos_key=ref_key, thumb_key=ref_thumb)
    listable_backend.seed(ref_key, age_days=40)
    listable_backend.seed(ref_thumb, age_days=40)
    # 软删内容仍被引用（30 天清理契约未到期前不得抢跑）
    soft_key = f"photos/{user.id}/202606/soft.jpg"
    _add_content(db, user, cos_key=soft_key, deleted=True)
    listable_backend.seed(soft_key, age_days=40)
    # 未超龄对象 → 不删
    listable_backend.seed(f"photos/{user.id}/202608/fresh.jpg", age_days=1)
    # 域外前缀 → 不扫不删
    listable_backend.seed(f"other/{user.id}/outofscope", age_days=40)

    report = run_orphan_scan(older_than_days=30, backend=listable_backend)

    assert report["skipped"] is False
    assert report["scanned_orphans"] == 3
    assert report["deleted"] == 3
    assert report["failed"] == 0
    assert report["by_category"] == {"staging": 1, "unregistered": 1, "wechat": 1}
    # 三个孤儿对象已删
    orphan_suffixes = ("0.part", "orphan.jpg", "msg123.jpg")
    survivors = [
        k for k in listable_backend._mtime
        if not any(k.endswith(suf) for suf in orphan_suffixes)
    ]
    assert len(listable_backend._mtime) == len(survivors)
    # 被引用 / 软删引用 / 未超龄 / 域外 均保留
    for kept in (ref_key, ref_thumb, soft_key, f"photos/{user.id}/202608/fresh.jpg",
                 f"other/{user.id}/outofscope"):
        assert listable_backend.object_exists(kept)


def test_orphan_scan_active_upload_kept(db_user, listable_backend):
    db, user = db_user
    tid = str(uuid.uuid4())
    _add_upload_task(db, user, tid, status="uploading", days_ago=1)
    listable_backend.seed(f"uploads/{tid}/0.part", age_days=1)

    report = run_orphan_scan(older_than_days=30, backend=listable_backend)

    assert report["scanned_orphans"] == 0
    assert report["deleted"] == 0
    assert listable_backend.object_exists(f"uploads/{tid}/0.part")


def test_orphan_scan_abandoned_and_completed_residue_deleted(db_user, listable_backend):
    db, user = db_user
    # 已放弃上传：任务未完成且超龄 → staging 孤儿
    abandoned_id = str(uuid.uuid4())
    _add_upload_task(db, user, abandoned_id, status="uploading", days_ago=40)
    listable_backend.seed(f"uploads/{abandoned_id}/0.part", age_days=40)
    # 已完成任务的分片残留：超龄 → 删
    completed_id = str(uuid.uuid4())
    _add_upload_task(db, user, completed_id, status="completed", days_ago=40)
    listable_backend.seed(f"uploads/{completed_id}/0.part", age_days=40)

    report = run_orphan_scan(older_than_days=30, backend=listable_backend)

    assert report["scanned_orphans"] == 2
    assert report["deleted"] == 2
    assert report["by_category"]["staging"] == 2
    assert not listable_backend.object_exists(f"uploads/{abandoned_id}/0.part")
    assert not listable_backend.object_exists(f"uploads/{completed_id}/0.part")


def test_orphan_scan_dry_run_reports_without_deleting(db_user, listable_backend):
    db, user = db_user
    listable_backend.seed(f"photos/{user.id}/202606/orphan.jpg", age_days=40)

    report = run_orphan_scan(older_than_days=30, dry_run=True, backend=listable_backend)

    assert report["scanned_orphans"] == 1
    assert report["deleted"] == 0
    assert report["dry_run"] is True
    assert listable_backend.object_exists(f"photos/{user.id}/202606/orphan.jpg")


def test_orphan_scan_idempotent(db_user, listable_backend):
    db, user = db_user
    listable_backend.seed(f"photos/{user.id}/202606/orphan.jpg", age_days=40)

    r1 = run_orphan_scan(older_than_days=30, backend=listable_backend)
    assert r1["deleted"] == 1
    # 二次运行：对象已删不再列出 → 无孤儿可扫
    r2 = run_orphan_scan(older_than_days=30, backend=listable_backend)
    assert r2["scanned_orphans"] == 0
    assert r2["deleted"] == 0


def test_orphan_scan_single_failure_does_not_abort(db_user, listable_backend):
    db, user = db_user
    k1 = f"photos/{user.id}/202606/o1.jpg"
    k2 = f"photos/{user.id}/202606/o2.jpg"
    k3 = f"photos/{user.id}/202606/o3.jpg"
    for k in (k1, k2, k3):
        listable_backend.seed(k, age_days=40)
    listable_backend._fail_delete.add(k2)

    report = run_orphan_scan(older_than_days=30, backend=listable_backend)

    assert report["scanned_orphans"] == 3
    assert report["deleted"] == 2
    assert report["failed"] == 1
    # 失败对象保留，其余已删
    assert listable_backend.object_exists(k2)
    assert not listable_backend.object_exists(k1)
    assert not listable_backend.object_exists(k3)
    # 故障恢复后重跑可清掉失败对象
    listable_backend._fail_delete.discard(k2)
    r2 = run_orphan_scan(older_than_days=30, backend=listable_backend)
    assert r2["deleted"] == 1
    assert not listable_backend.object_exists(k2)


def test_orphan_scan_respects_limit(db_user, listable_backend):
    db, user = db_user
    for i in range(3):
        listable_backend.seed(f"photos/{user.id}/202606/o{i}.jpg", age_days=40)

    report = run_orphan_scan(older_than_days=30, limit=1, backend=listable_backend)

    assert report["scanned_orphans"] == 3  # 全量候选
    assert report["deleted"] == 1  # 本轮只处理 limit 个
    assert report["failed"] == 0


def test_orphan_scan_skips_when_backend_lacks_list_objects(db_user):
    """B3 未合入（后端无 list_objects）→ skipped 报告，不崩"""
    from app.services.external.storage import FakeStorageBackend

    report = run_orphan_scan(older_than_days=30, backend=FakeStorageBackend())

    assert report["skipped"] is True
    assert report["deleted"] == 0
    assert report["failed"] == 0
    assert report["by_category"] == {"staging": 0, "unregistered": 0, "wechat": 0}


def test_orphan_scan_accepts_object_attribute_listing(db_user, listable_backend):
    """B3 返回带 .key/.last_modified 属性的对象形状（非 tuple）也能消费"""
    db, user = db_user

    class _Obj:
        def __init__(self, key, lm):
            self.key = key
            self.last_modified = lm

    orphan = f"photos/{user.id}/202606/attr.jpg"
    listable_backend.put_object(orphan, b"x")
    listable_backend._mtime[orphan] = NOW - timedelta(days=40)
    # 用属性形状的 list_objects 替换（验证归一容忍）
    attr_keys = {orphan}

    def _attr_listing(prefix):
        return [
            _Obj(k, listable_backend._mtime[k])
            for k in attr_keys
            if k.startswith(prefix)
        ]

    listable_backend.list_objects = _attr_listing

    report = run_orphan_scan(older_than_days=30, backend=listable_backend)

    assert report["scanned_orphans"] == 1
    assert report["deleted"] == 1
