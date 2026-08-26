"""F1/P0-6 照片双轨收口 + F4 job 级去重测试（2026-08-27 · 批次 F-Content）

锚定：
  - 双幂等键：perceptual_hash 409（multipart 路径）/ cos_key 幂等（分片路径）
  - photo_content.register_photo_content 参数化 dedup_key/moderate/mode 唯一编排
    （original/thumbnail_meta/update 三模式）
  - enqueue_unique 同键不重复入队（单测，mock Redis，不需 DB）
前置：PG yishu 库（db_user 公共 fixture；fake 存储由 conftest autouse 强制）
"""
import io
import json
import uuid

import pytest
from app.db.models import Content
from app.services import photo_content as pc
from app.services.external.storage import get_storage_backend
from app.services.upload_meta import parse_photo_meta

pytestmark = pytest.mark.integration


def _jpeg_bytes() -> bytes:
    """有效 JPEG（魔数校验 / resize 需要可解码图片）"""
    from PIL import Image

    img = Image.new("RGB", (600, 400), (10, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _seed_photo_key(user, suffix: str = "x") -> str:
    """把有效 JPEG 落 fake 存储，返回 cos_key（模拟分片 complete 后对象已在）"""
    key = f"photos/{user.id}/202608/{suffix}_{uuid.uuid4().hex[:8]}.jpg"
    get_storage_backend().put_object(key, _jpeg_bytes())
    return key


def _pm(meta: dict):
    return parse_photo_meta(json.dumps(meta, ensure_ascii=False))


# ---------- enqueue_unique 单测（F4/R5-4#5，mock Redis） ----------


def _fake_queue_tooling(monkeypatch, enqueued: list[str]) -> None:
    """mock get_queue/redis.set/get_job，收集实际 enqueue 的 job_id"""
    import app.core.queue as queue_mod

    class FakeQueue:
        def enqueue(self, func, *args, **kwargs):
            enqueued.append(kwargs["job_id"])
            return {"job_id": kwargs["job_id"]}

    monkeypatch.setattr(queue_mod, "get_queue", lambda name: FakeQueue())


class _JobLike:
    """RQ Job 的测试替身：get_status 返回指定状态"""

    def __init__(self, status: str = "queued"):
        self._status = status

    def get_status(self):
        return self._status


def test_enqueue_unique_same_key_no_double_enqueue(monkeypatch):
    """F4：同 key 不重复入队——SETNX 第二次失败 → 复用既有 job，不重复 enqueue"""
    import app.core.queue as queue_mod

    enqueued: list[str] = []
    _fake_queue_tooling(monkeypatch, enqueued)
    state = {"nx_ok": True}
    monkeypatch.setattr(
        queue_mod.redis,
        "set",
        lambda k, v, **kw: state.pop("nx_ok", False) if kw.get("nx") else True,
    )
    monkeypatch.setattr(queue_mod, "get_job", lambda jid: _JobLike("queued"))

    def my_job(x): ...

    j1 = queue_mod.enqueue_unique(my_job, "content-1", "arg")
    j2 = queue_mod.enqueue_unique(my_job, "content-1", "arg")
    assert len(enqueued) == 1, "同 key 不应重复入队"
    assert enqueued[0] == "my_job_content-1"
    assert j1["job_id"] == "my_job_content-1"
    assert j2.get_status() == "queued"  # 二次调用返回既有 job（未新建）


def test_enqueue_unique_distinct_keys_both_enqueue(monkeypatch):
    """F4：不同 key 各自独立入队（job_id 含 key，不互相覆盖）"""
    import app.core.queue as queue_mod

    enqueued: list[str] = []
    _fake_queue_tooling(monkeypatch, enqueued)
    monkeypatch.setattr(queue_mod.redis, "set", lambda k, v, **kw: True)
    monkeypatch.setattr(queue_mod, "get_job", lambda jid: None)

    def my_job(x): ...

    queue_mod.enqueue_unique(my_job, "c-1")
    queue_mod.enqueue_unique(my_job, "c-2")
    assert enqueued == ["my_job_c-1", "my_job_c-2"]


def test_enqueue_unique_rebuilds_after_failed_job(monkeypatch):
    """F4：既有 job 已 failed → 重建（RQ 同 job_id 覆盖，防失败后永久静默）"""
    import app.core.queue as queue_mod

    enqueued: list[str] = []
    _fake_queue_tooling(monkeypatch, enqueued)
    monkeypatch.setattr(queue_mod.redis, "set", lambda k, v, **kw: False)  # 占位失败
    monkeypatch.setattr(queue_mod, "get_job", lambda jid: _JobLike("failed"))

    def my_job(x): ...

    queue_mod.enqueue_unique(my_job, "c-1")
    assert enqueued == ["my_job_c-1"]


def test_enqueue_unique_passes_queue_and_timeout(monkeypatch):
    """F4：queue_name/job_timeout 透传 RQ enqueue（低优情绪任务契约）"""
    import app.core.queue as queue_mod

    captured: dict = {}
    got_names: list[str] = []

    class FakeQueue:
        def enqueue(self, func, *args, **kwargs):
            captured.update(kwargs)
            return {"job_id": kwargs["job_id"]}

    monkeypatch.setattr(
        queue_mod, "get_queue", lambda name: got_names.append(name) or FakeQueue()
    )
    monkeypatch.setattr(queue_mod.redis, "set", lambda k, v, **kw: True)

    def my_job(x): ...

    queue_mod.enqueue_unique(my_job, "c-1", queue_name="low", job_timeout=300)
    assert got_names == ["low"]
    assert captured["job_timeout"] == 300
    assert captured["job_id"] == "my_job_c-1"
    assert captured["retry"].max == 3  # 与既有 RETRY_POLICY 对齐


def test_enqueue_unique_sanitizes_unsafe_key(monkeypatch):
    """集成修复：key 含冒号/空格/中文等非法字符 → job_id 净化，不触发 RQ validate_job_id ValueError"""
    import app.core.queue as queue_mod

    enqueued: list[str] = []
    _fake_queue_tooling(monkeypatch, enqueued)
    monkeypatch.setattr(queue_mod.redis, "set", lambda k, v, **kw: True)

    def my_job(x): ...

    queue_mod.enqueue_unique(my_job, "content: 照片/1号")
    assert len(enqueued) == 1
    # 只允许 RQ 合法字符（字母/数字/下划线/连字符），且 key 段被净化
    assert all(c.isalnum() or c in "_-" for c in enqueued[0])
    assert enqueued[0].startswith("my_job_content")


def test_enqueue_idempotent_sanitizes_unsafe_job_id(monkeypatch):
    """集成修复：enqueue_idempotent 冒号 job_id 在 RQ 2.x 会 ValueError——真实 client_request_id
    （可含冒号/空格/中文）入队即炸；现各段净化后正常入队且幂等键基于净化结果。"""
    import app.core.queue as queue_mod

    enqueued: list[str] = []
    _fake_queue_tooling(monkeypatch, enqueued)
    monkeypatch.setattr(queue_mod.redis, "set", lambda k, v, **kw: True)

    def my_job(x): ...

    queue_mod.enqueue_idempotent("classify", "user-1", "req: 照片/1号", my_job)
    assert len(enqueued) == 1
    assert all(c.isalnum() or c in "_-" for c in enqueued[0])
    assert enqueued[0].startswith("classify_user-1_")


# ---------- 双幂等键锚定（F1/P0-6） ----------


def test_dedup_perceptual_hash_409(db_user):
    """multipart 幂等键：同用户同 perceptual_hash → DuplicateError（409 语义）"""
    db, user = db_user
    pm = _pm({"source": "app", "perceptual_hash": "ph-anchor-001"})
    kwargs = dict(
        dedup_key="perceptual_hash",
        moderate=True,
        mode="original",
        meta_obj=pm.raw,
        photo_meta=pm,
        perceptual_hash="ph-anchor-001",
        data=_jpeg_bytes(),
        ext=".jpg",
    )
    cid1 = pc.register_photo_content(db, user.id, **kwargs)
    assert cid1
    with pytest.raises(pc.DuplicateError):
        pc.register_photo_content(db, user.id, **kwargs)


def test_dedup_cos_key_idempotent(db_user):
    """分片幂等键：同用户同 cos_key → 返回既有 content_id（P0-5）"""
    db, user = db_user
    key = _seed_photo_key(user, "idem")
    pm = _pm({"source": "app"})
    kwargs = dict(
        dedup_key="cos_key",
        moderate=False,
        mode="original",
        meta_obj=pm.raw,
        photo_meta=pm,
        cos_key=key,
        enqueue_thumbnail=True,
    )
    cid1 = pc.register_photo_content(db, user.id, **kwargs)
    cid2 = pc.register_photo_content(db, user.id, **kwargs)
    assert cid1 == cid2
    rows = db.query(Content).filter(
        Content.user_id == user.id, Content.cos_key == key
    ).all()
    assert len(rows) == 1, "cos_key 幂等：同键只应建一条内容"


def test_moderate_reject_raises(db_user):
    """moderate=True 且 meta.text 命中敏感 → ModerateRejectError（422 语义）"""
    db, user = db_user
    pm = _pm({"text": "支持法轮功的言论", "source": "app"})
    with pytest.raises(pc.ModerateRejectError):
        pc.register_photo_content(
            db,
            user.id,
            dedup_key=None,
            moderate=True,
            mode="original",
            meta_obj=pm.raw,
            photo_meta=pm,
            data=_jpeg_bytes(),
        )


def test_original_multipart_stores_and_creates(db_user):
    """original + data：原件落 storage + 建 photo processing 记录（multipart 路径）"""
    db, user = db_user
    pm = _pm({"source": "app"})
    cid = pc.register_photo_content(
        db,
        user.id,
        dedup_key=None,
        moderate=False,
        mode="original",
        meta_obj=pm.raw,
        photo_meta=pm,
        data=_jpeg_bytes(),
        ext=".jpg",
    )
    record = db.get(Content, cid)
    assert record is not None
    assert record.content_type == "photo"
    assert record.status == "processing"
    assert record.cos_key.startswith(f"photos/{user.id}/")
    assert get_storage_backend().object_exists(record.cos_key)


def test_thumbnail_meta_placeholder(db_user):
    """thumbnail_meta：只落缩略图占位（status=done，不进管线，等 WiFi 补传）"""
    db, user = db_user
    key = _seed_photo_key(user, "thumbmeta")
    pm = _pm({"upload_mode": "thumbnail_meta", "source": "app"})
    cid = pc.register_photo_content(
        db,
        user.id,
        dedup_key="cos_key",
        moderate=False,
        mode="thumbnail_meta",
        meta_obj=pm.raw,
        photo_meta=pm,
        cos_key=key,
        enqueue_thumbnail=True,
    )
    record = db.get(Content, cid)
    assert record.status == "done"
    assert record.thumbnail_key is not None
    assert record.extra.get("original_pending") is True
    assert get_storage_backend().object_exists(record.thumbnail_key)


def test_update_mode_links_original(db_user):
    """update：content_id 补传原件挂到占位内容（复用 complete 手动补传原图）"""
    db, user = db_user
    thumb_key = _seed_photo_key(user, "placeholder")
    pm_thumb = _pm({"upload_mode": "thumbnail_meta", "source": "app"})
    placeholder_id = pc.register_photo_content(
        db,
        user.id,
        dedup_key="cos_key",
        moderate=False,
        mode="thumbnail_meta",
        meta_obj=pm_thumb.raw,
        photo_meta=pm_thumb,
        cos_key=thumb_key,
        enqueue_thumbnail=True,
    )
    original_key = _seed_photo_key(user, "original")
    pm_update = _pm({"content_id": placeholder_id, "source": "app"})
    cid = pc.register_photo_content(
        db,
        user.id,
        dedup_key="cos_key",
        moderate=False,
        mode="update",
        meta_obj=pm_update.raw,
        photo_meta=pm_update,
        cos_key=original_key,
        content_id=placeholder_id,
        enqueue_thumbnail=True,
    )
    assert cid == placeholder_id
    record = db.get(Content, cid)
    assert record.cos_key == original_key
    assert record.status == "processing"
    assert record.extra.get("original_pending") is None
    assert record.thumbnail_key is not None  # 占位缩略图保留
