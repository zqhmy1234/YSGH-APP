"""孤儿对象扫描 job 测试（B1 · Wave1 · P0-6 缺口补口 · 收尾 §5.2.6 · 任务卡 06 v2）

覆盖（任务卡 §3 测试矩阵）：
  - 只删孤儿：三类孤儿（staging 分片 / 未注册 cos_key / wechat 拦截对象）超龄 + 零引用 → 删；
    已引用（cos_key/thumbnail_key，含软删行）不动
  - 未超龄不删：未引用但 mtime 新 → 不删
  - staging 有任务不删：upload_tasks 存在未完成记录（年轻）→ 保留可续传
  - staging 已放弃/已完成残留：任务未完成且超龄 / 已完成分片残留 → 删
  - R2 保护：upload_tasks 已完成记录的 file_key → 视为引用不删（完成→注册空窗期）
  - 双阈值：staging 1 天 / object 30 天各自生效
  - dry-run 不删：统计正确、零删除
  - 幂等重跑：第一轮删后第二轮 orphans_total=0
  - 单失败不中断：delete 抛异常（best_effort_delete 吞）→ 校验仍存在计 failed，继续后续
  - limit 生效：孤儿 3 个 limit=1 → 只处理 1 个
  - 返回统计字段：六字段齐全且数值自洽
  - 后端无 list_objects（B3 未合入）→ skipped 报告不崩
  - 属性形状 list_objects（.key/.last_modified）→ 归一消费
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

    def seed(self, key: str, age_days: float) -> None:
        """写对象并设 last_modified = NOW - age_days 天（构造超龄/未超龄；支持小数天）"""
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


def _add_upload_task(db, user, upload_id, status, days_ago, file_key=None):
    task = UploadTask(
        id=upload_id,
        user_id=user.id,
        client_upload_id=f"cli-{upload_id[:8]}",
        file_name="a.jpg",
        file_size=1000,
        chunk_size=500,
        chunk_count=2,
        file_key=file_key or f"photos/{user.id}/202607/a.jpg",
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
    # 未超龄对象（object 阈值 30 天）→ 不删
    listable_backend.seed(f"photos/{user.id}/202608/fresh.jpg", age_days=1)
    # 域外前缀 → 不扫不删
    listable_backend.seed(f"other/{user.id}/outofscope", age_days=40)

    report = run_orphan_scan(backend=listable_backend)

    assert report["skipped"] is False
    assert report["scanned_keys"] == 7
    assert report["orphans_total"] == 3
    assert report["orphans_staging"] == 1
    assert report["orphans_unregistered"] == 1
    assert report["orphans_wechat"] == 1
    assert report["deleted"] == 3
    assert report["failed"] == 0
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


def test_orphan_scan_returns_six_stat_fields_consistent(db_user, listable_backend):
    db, user = db_user
    listable_backend.seed(f"uploads/{uuid.uuid4()}/0.part", age_days=2)
    listable_backend.seed(f"photos/{user.id}/202606/a.jpg", age_days=40)
    listable_backend.seed(f"wechat/{user.id}/m.jpg", age_days=40)
    # 未超龄对象：仅消耗 scanned_keys，不进孤儿
    listable_backend.seed(f"photos/{user.id}/202608/b.jpg", age_days=1)

    report = run_orphan_scan(backend=listable_backend)

    for field in ("scanned_keys", "orphans_total", "orphans_staging",
                  "orphans_unregistered", "orphans_wechat", "deleted", "failed"):
        assert field in report, f"缺统计字段: {field}"
    assert report["scanned_keys"] == 4
    assert report["orphans_total"] == (
        report["orphans_staging"] + report["orphans_unregistered"] + report["orphans_wechat"]
    )
    assert report["deleted"] == report["orphans_total"]
    assert report["failed"] == 0


def test_orphan_scan_active_upload_kept(db_user, listable_backend):
    db, user = db_user
    tid = str(uuid.uuid4())
    _add_upload_task(db, user, tid, status="uploading", days_ago=0)
    # 任务年轻（今天创建）即使分片旧也保留（活跃上传可续传）
    listable_backend.seed(f"uploads/{tid}/0.part", age_days=40)

    report = run_orphan_scan(backend=listable_backend)

    assert report["orphans_total"] == 0
    assert report["deleted"] == 0
    assert listable_backend.object_exists(f"uploads/{tid}/0.part")


def test_orphan_scan_abandoned_and_completed_residue_deleted(db_user, listable_backend):
    db, user = db_user
    # 已放弃上传：任务未完成且超龄（>1 天）→ staging 孤儿
    abandoned_id = str(uuid.uuid4())
    _add_upload_task(db, user, abandoned_id, status="uploading", days_ago=3)
    listable_backend.seed(f"uploads/{abandoned_id}/0.part", age_days=3)
    # 已完成任务的分片残留：超龄（>1 天）→ 删
    completed_id = str(uuid.uuid4())
    _add_upload_task(db, user, completed_id, status="completed", days_ago=3)
    listable_backend.seed(f"uploads/{completed_id}/0.part", age_days=3)

    report = run_orphan_scan(backend=listable_backend)

    assert report["orphans_total"] == 2
    assert report["orphans_staging"] == 2
    assert report["deleted"] == 2
    assert not listable_backend.object_exists(f"uploads/{abandoned_id}/0.part")
    assert not listable_backend.object_exists(f"uploads/{completed_id}/0.part")


def test_orphan_scan_completed_task_file_key_referenced(db_user, listable_backend):
    """R2 保护：已完成上传任务的 file_key 视为引用，不因无 contents 引用被误删"""
    db, user = db_user
    tid = str(uuid.uuid4())
    final_key = f"photos/{user.id}/202607/final.jpg"
    _add_upload_task(db, user, tid, status="completed", days_ago=3, file_key=final_key)
    listable_backend.seed(final_key, age_days=40)  # 超龄但属 R2 引用

    report = run_orphan_scan(backend=listable_backend)

    assert report["orphans_total"] == 0
    assert report["deleted"] == 0
    assert listable_backend.object_exists(final_key)


def test_orphan_scan_dual_thresholds(db_user, listable_backend):
    """双阈值各自生效：staging 1 天 / object 30 天"""
    db, user = db_user
    # staging 2 天 > 1 → 孤儿
    listable_backend.seed(f"uploads/{uuid.uuid4()}/0.part", age_days=2)
    # object 2 天 < 30 → 不删（未注册但未超龄）
    listable_backend.seed(f"photos/{user.id}/202608/young.jpg", age_days=2)
    # object 40 天 > 30 → 孤儿
    listable_backend.seed(f"photos/{user.id}/202606/old.jpg", age_days=40)

    report = run_orphan_scan(backend=listable_backend)

    assert report["orphans_total"] == 2  # staging 2d + object 40d
    assert report["orphans_staging"] == 1
    assert report["orphans_unregistered"] == 1
    assert report["deleted"] == 2
    assert listable_backend.object_exists(f"photos/{user.id}/202608/young.jpg")


def test_orphan_scan_dry_run_reports_without_deleting(db_user, listable_backend):
    db, user = db_user
    listable_backend.seed(f"photos/{user.id}/202606/orphan.jpg", age_days=40)

    report = run_orphan_scan(dry_run=True, backend=listable_backend)

    assert report["orphans_total"] == 1
    assert report["deleted"] == 0
    assert report["failed"] == 0
    assert report["dry_run"] is True
    assert listable_backend.object_exists(f"photos/{user.id}/202606/orphan.jpg")


def test_orphan_scan_idempotent(db_user, listable_backend):
    db, user = db_user
    listable_backend.seed(f"photos/{user.id}/202606/orphan.jpg", age_days=40)

    r1 = run_orphan_scan(backend=listable_backend)
    assert r1["deleted"] == 1
    # 二次运行：对象已删不再列出 → 无孤儿可扫
    r2 = run_orphan_scan(backend=listable_backend)
    assert r2["orphans_total"] == 0
    assert r2["deleted"] == 0


def test_orphan_scan_single_failure_does_not_abort(db_user, listable_backend):
    db, user = db_user
    k1 = f"photos/{user.id}/202606/o1.jpg"
    k2 = f"photos/{user.id}/202606/o2.jpg"
    k3 = f"photos/{user.id}/202606/o3.jpg"
    for k in (k1, k2, k3):
        listable_backend.seed(k, age_days=40)
    listable_backend._fail_delete.add(k2)

    report = run_orphan_scan(backend=listable_backend)

    assert report["orphans_total"] == 3
    assert report["deleted"] == 2
    assert report["failed"] == 1
    # 失败对象保留（best_effort_delete 吞异常 → 删后校验仍存在计 failed），其余已删
    assert listable_backend.object_exists(k2)
    assert not listable_backend.object_exists(k1)
    assert not listable_backend.object_exists(k3)
    # 故障恢复后重跑可清掉失败对象
    listable_backend._fail_delete.discard(k2)
    r2 = run_orphan_scan(backend=listable_backend)
    assert r2["deleted"] == 1
    assert not listable_backend.object_exists(k2)


def test_orphan_scan_respects_limit(db_user, listable_backend):
    db, user = db_user
    for i in range(3):
        listable_backend.seed(f"photos/{user.id}/202606/o{i}.jpg", age_days=40)

    report = run_orphan_scan(limit=1, backend=listable_backend)

    assert report["orphans_total"] == 3  # 全量候选
    assert report["deleted"] == 1  # 本轮只处理 limit 个
    assert report["failed"] == 0


def test_orphan_scan_skips_when_backend_lacks_list_objects(db_user):
    """B3 未合入（后端无 list_objects）→ skipped 报告，不崩"""
    from app.services.external.storage import FakeStorageBackend

    report = run_orphan_scan(backend=FakeStorageBackend())

    assert report["skipped"] is True
    assert report["scanned_keys"] == 0
    assert report["orphans_total"] == 0
    assert report["deleted"] == 0
    assert report["failed"] == 0


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

    report = run_orphan_scan(backend=listable_backend)

    assert report["orphans_total"] == 1
    assert report["deleted"] == 1
