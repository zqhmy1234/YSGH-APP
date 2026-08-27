"""分片上传状态机测试（S5-03 · WP-C）

覆盖：
  - init 幂等（同 client_upload_id 复用任务）
  - 分片上传：正常 / 幂等重复（duplicate）/ 同 index 异 hash 拒绝 / index 越界 / hash 不匹配
  - 断点续传：缺片列表正确，补传后 complete 成功
  - complete：分片未齐拒绝 / 合并大小校验 / 幂等重复 complete / staging 清理
  - 存储后端：默认 fake（内存）
前置：PG yishu 库
"""
import uuid

import pytest
from app.core.config import settings
from app.db.models import Content, UploadChunk, User
from app.services import upload as upload_svc
from app.services.external.storage import get_storage_backend
from sqlalchemy import select

pytestmark = pytest.mark.integration

CHUNK = 1024  # 1KB 分片便于测试


def _make_task(db, user, client_upload_id=None, data: bytes | None = None, chunk_size=CHUNK):
    data = data if data is not None else b"a" * 2500
    task = upload_svc.init_upload(
        db,
        user.id,
        client_upload_id or f"cid-{uuid.uuid4().hex[:10]}",
        "test.jpg",
        len(data),
        chunk_size,
    )
    return task, data


def test_init_idempotent(db_user):
    db, user = db_user
    t1, data = _make_task(db, user)
    t2 = upload_svc.init_upload(db, user.id, t1.client_upload_id, "test.jpg", len(data), CHUNK)
    assert t1.id == t2.id
    assert t2.chunk_count == 3  # 2500 / 1024 → 3


def test_complete_creates_content(db_user):
    """S-ST-1 集成：complete 后 register_photo_content 建 contents 记录并返回 content_id"""
    db, user = db_user
    task, data = _make_task(db, user, data=_jpeg_bytes())
    for i in range(task.chunk_count):
        part = data[i * CHUNK : (i + 1) * CHUNK]
        upload_svc.upload_chunk(db, task.id, i, part)
    result = upload_svc.complete_upload(db, task.id)

    content_id = upload_svc.register_photo_content(
        db,
        user.id,
        result["file_key"],
        '{"taken_at":"2026-08-24T12:00:00+08:00","gps_lat":31.2304,"gps_lng":121.4737,"source":"app"}',
    )
    record = db.get(Content, content_id)
    assert record is not None
    assert record.content_type == "photo"
    assert record.cos_key == result["file_key"]
    assert record.status == "processing"
    assert record.gps_lat == 31.2304
    assert record.gps_lng == 121.4737


def test_register_photo_content_bad_meta(db_user):
    """meta 非法（坏 JSON / gps 越界 / source 白名单外）→ ValueError"""
    db, user = db_user
    with pytest.raises(ValueError):
        upload_svc.register_photo_content(db, user.id, "photos/x.jpg", "{not-json")
    with pytest.raises(ValueError):
        upload_svc.register_photo_content(db, user.id, "photos/x.jpg", '{"gps_lat": 999}')
    with pytest.raises(ValueError):
        upload_svc.register_photo_content(db, user.id, "photos/x.jpg", '{"source": "hacker"}')



    db, user = db_user
    task, data = _make_task(db, user)
    chunk_count = task.chunk_count
    for i in range(chunk_count):
        part = data[i * CHUNK : (i + 1) * CHUNK]
        result = upload_svc.upload_chunk(db, task.id, i, part)
        assert result["status"] == "uploaded"
    result = upload_svc.complete_upload(db, task.id)
    assert result["status"] == "completed"
    # 最终对象存在且内容一致（fake 后端）
    from app.services.external.storage import get_storage_backend

    backend = get_storage_backend("fake")
    assert backend.get_object(result["file_key"]) == data
    # staging 已清理
    assert not backend.object_exists(f"uploads/{task.id}/0.part")


def test_chunk_duplicate_idempotent(db_user):
    db, user = db_user
    task, data = _make_task(db, user)
    part = data[:CHUNK]
    upload_svc.upload_chunk(db, task.id, 0, part)
    r2 = upload_svc.upload_chunk(db, task.id, 0, part)
    assert r2["status"] == "duplicate"


def test_chunk_concurrent_upload_no_500(db_user):
    """R2#13 竞态修复：同片并发上传不 500（ON CONFLICT DO NOTHING 原子兜底）

    两会话同时上传同一片：一个 uploaded，另一个 rowcount=0 → duplicate，仅一行 chunk。
    """
    import threading

    from app.db.session import SessionLocal

    db, user = db_user
    task, data = _make_task(db, user)
    part = data[:CHUNK]
    results: dict = {}

    def worker(n: int):
        s = SessionLocal()
        try:
            r = upload_svc.upload_chunk(s, task.id, 0, part)
            results[n] = r["status"]
        except Exception as exc:  # noqa: BLE001 —— 记录逃逸异常（不应发生）
            results[n] = exc
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(v in ("uploaded", "duplicate") for v in results.values()), results
    chunks = db.execute(
        select(UploadChunk).where(UploadChunk.upload_id == task.id)
    ).scalars().all()
    assert len(chunks) == 1, "同片并发只应有一行 chunk 记录"


def test_chunk_same_index_diff_hash_rejected(db_user):
    db, user = db_user
    task, data = _make_task(db, user)
    upload_svc.upload_chunk(db, task.id, 0, b"x" * CHUNK)
    with pytest.raises(ValueError):
        upload_svc.upload_chunk(db, task.id, 0, b"y" * CHUNK)


def test_chunk_hash_mismatch_rejected(db_user):
    db, user = db_user
    task, data = _make_task(db, user)
    with pytest.raises(ValueError):
        upload_svc.upload_chunk(db, task.id, 0, b"abc", chunk_hash="deadbeef")


def test_chunk_index_out_of_range(db_user):
    db, user = db_user
    task, data = _make_task(db, user)
    with pytest.raises(ValueError):
        upload_svc.upload_chunk(db, task.id, 99, b"x")


def test_resume_missing_chunks(db_user):
    """断电续传：只传 0、2 片 → 缺失 [1] → 补传后 complete"""
    db, user = db_user
    task, data = _make_task(db, user)
    for i in (0, 2):
        part = data[i * CHUNK : (i + 1) * CHUNK]
        upload_svc.upload_chunk(db, task.id, i, part)
    status = upload_svc.get_status(db, task.id)
    assert status["missing_chunks"] == [1]
    part1 = data[CHUNK : 2 * CHUNK]
    upload_svc.upload_chunk(db, task.id, 1, part1)
    result = upload_svc.complete_upload(db, task.id)
    assert result["status"] == "completed"


def test_complete_with_missing_chunks_rejected(db_user):
    db, user = db_user
    task, data = _make_task(db, user)
    upload_svc.upload_chunk(db, task.id, 0, data[:CHUNK])
    with pytest.raises(ValueError):
        upload_svc.complete_upload(db, task.id)


def test_complete_idempotent(db_user):
    db, user = db_user
    task, data = _make_task(db, user)
    for i in range(task.chunk_count):
        upload_svc.upload_chunk(db, task.id, i, data[i * CHUNK : (i + 1) * CHUNK])
    upload_svc.complete_upload(db, task.id)
    r2 = upload_svc.complete_upload(db, task.id)
    assert r2["status"] == "completed"


def test_default_backend_is_fake():
    assert settings.storage_backend == "fake"


def test_chunk_larger_than_declared_rejected(db_user):
    """审查修复(P1-02)：单片超过声明分片大小 → 拒绝（防内存/存储滥用）"""
    db, user = db_user
    task, data = _make_task(db, user, chunk_size=1024)
    # 传 2KB 单片（声明 1KB）
    with pytest.raises(ValueError):
        upload_svc.upload_chunk(db, task.id, 0, b"x" * 2048)
    # 正常大小不受影响
    r = upload_svc.upload_chunk(db, task.id, 0, b"x" * 1024)
    assert r["status"] == "uploaded"


def test_cross_user_access_denied(db_user, cleanup_user):
    """审查 CRITICAL 修复（IDOR）：用户 B 无法操作用户 A 的上传任务

    upload_chunk / get_status / complete_upload 三个入口均需归属校验，
    他人任务一律视为不存在（KeyError → 404）。
    """
    db, user_a = db_user
    # 用户 B（攻击者）
    user_b = User(phone=f"upload-attacker-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user_b)
    db.commit()
    db.refresh(user_b)
    try:
        task, data = _make_task(db, user_a)
        # ① 越权传分片
        with pytest.raises(KeyError):
            upload_svc.upload_chunk(db, task.id, 0, data[:CHUNK], user_id=user_b.id)
        # ② 越权查状态
        with pytest.raises(KeyError):
            upload_svc.get_status(db, task.id, user_id=user_b.id)
        # ③ 越权 complete
        with pytest.raises(KeyError):
            upload_svc.complete_upload(db, task.id, user_id=user_b.id)
        # ④ 本人仍可正常操作（未误伤）
        upload_svc.upload_chunk(db, task.id, 0, data[:CHUNK], user_id=user_a.id)
        assert upload_svc.get_status(db, task.id, user_id=user_a.id)["uploaded_chunks"] == [0]
    finally:
        # 清理 B 用户（A 的清理走公共 db_user fixture；R8#2：统一 cleanup_user_data）
        cleanup_user(db, user_b.id)
        db.delete(user_b)
        db.commit()


# ---------- Wave3 AgentG：流量约束（B4 §6）upload_mode / 手动补传原图 ----------

def _jpeg_bytes() -> bytes:
    """有效 JPEG（thumbnail_meta 路径 resize_to_jpeg 需要可解码图片）"""
    import io

    from PIL import Image

    img = Image.new("RGB", (600, 400), (10, 120, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def test_register_photo_content_thumbnail_meta(db_user):
    """蜂窝路径：upload_mode=thumbnail_meta → 只落缩略图占位内容（original_pending）"""
    db, user = db_user
    jpeg = _jpeg_bytes()
    # 模拟 complete 已把"缩略图"合并到最终对象
    cos_key = f"photos/{user.id}/202608/thumbmeta_{uuid.uuid4().hex[:8]}.jpg"
    from app.services.external.storage import get_storage_backend

    get_storage_backend().put_object(cos_key, jpeg)

    content_id = upload_svc.register_photo_content(
        db,
        user.id,
        cos_key,
        '{"upload_mode":"thumbnail_meta","on_wifi":false,"source":"app"}',
    )
    record = db.get(Content, content_id)
    assert record is not None
    assert record.thumbnail_key == f"thumbnails/{cos_key.split('/', 1)[1]}"
    assert record.extra["upload_mode"] == "thumbnail_meta"
    assert record.extra["on_wifi"] is False
    assert record.extra["original_pending"] is True
    assert record.status == "done"  # 占位即可浏览，不进管线
    assert get_storage_backend().object_exists(record.thumbnail_key)


def test_register_photo_content_invalid_upload_mode(db_user):
    """upload_mode 白名单外 → ValueError"""
    db, user = db_user
    with pytest.raises(ValueError):
        upload_svc.register_photo_content(
            db, user.id, "photos/x.jpg", '{"upload_mode":"hacker","source":"app"}'
        )
    with pytest.raises(ValueError):
        upload_svc.register_photo_content(
            db, user.id, "photos/x.jpg", '{"on_wifi":"not-bool","source":"app"}'
        )


def test_register_photo_content_manual_original_links_placeholder(db_user):
    """手动立即上传原图（复用 complete + meta.content_id）：挂原件到占位内容"""
    db, user = db_user
    # 先建 thumbnail_meta 占位
    jpeg = _jpeg_bytes()
    thumb_key = f"photos/{user.id}/202608/placeholder_{uuid.uuid4().hex[:8]}.jpg"
    backend = get_storage_backend()
    backend.put_object(thumb_key, jpeg)
    placeholder_id = upload_svc.register_photo_content(
        db, user.id, thumb_key, '{"upload_mode":"thumbnail_meta","source":"app"}'
    )

    # WiFi 后补传原件：走完整分片链路，meta 带 content_id
    original_key = f"photos/{user.id}/202608/original_{uuid.uuid4().hex[:8]}.jpg"
    backend.put_object(original_key, _jpeg_bytes())  # P0-3：原件需过魔数校验
    content_id = upload_svc.register_photo_content(
        db,
        user.id,
        original_key,
        f'{{"content_id":"{placeholder_id}","upload_mode":"original","on_wifi":true,"source":"app"}}',
    )
    assert content_id == placeholder_id  # 复用同一内容
    record = db.get(Content, content_id)
    assert record.cos_key == original_key  # 原件已挂
    assert record.thumbnail_key is not None  # 缩略图保留
    assert record.extra.get("original_pending") is None  # 占位标记清除
    assert record.extra["upload_mode"] == "original"
    assert record.extra["on_wifi"] is True
    assert record.status == "processing"  # 走完整管线


def test_register_photo_content_content_id_ownership(db_user, cleanup_user):
    """content_id 归属校验：他人内容 → ValueError（防 IDOR）"""
    db, user_a = db_user
    user_b = User(phone=f"upload-lnk-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user_b)
    db.commit()
    db.refresh(user_b)
    try:
        # B 的占位内容
        jpeg = _jpeg_bytes()
        key_b = f"photos/{user_b.id}/202608/ph_{uuid.uuid4().hex[:8]}.jpg"
        get_storage_backend().put_object(key_b, jpeg)
        placeholder_b = upload_svc.register_photo_content(
            db, user_b.id, key_b, '{"upload_mode":"thumbnail_meta","source":"app"}'
        )
        # A 试图用 content_id 挂自己原件到 B 的内容 → 拒绝
        with pytest.raises(ValueError):
            upload_svc.register_photo_content(
                db,
                user_a.id,
                "photos/a/x.jpg",
                f'{{"content_id":"{placeholder_b}","source":"app"}}',
            )
        # B 自己的占位不受影响
        assert db.get(Content, placeholder_b).cos_key == key_b
    finally:
        # R8#2：统一 cleanup_user_data（upload_tasks+chunks / contents 全链删）
        cleanup_user(db, user_b.id)
        db.delete(user_b)
        db.commit()


def test_complete_voice_branch(db_user):
    """B5a 集成：complete 后 register_photo_content 建 voice 内容（对象搬 voice/ 前缀 + 入队）"""
    db, user = db_user
    task, data = _make_task(db, user)
    for i in range(task.chunk_count):
        upload_svc.upload_chunk(db, task.id, i, data[i * CHUNK : (i + 1) * CHUNK])
    result = upload_svc.complete_upload(db, task.id)

    backend = get_storage_backend()
    content_id = upload_svc.register_photo_content(
        db,
        user.id,
        result["file_key"],
        '{"content_type":"voice","duration_ms":65000,"source":"app","extra":{"file_name":"rec_01.wav"}}',
    )
    record = db.get(Content, content_id)
    assert record is not None
    assert record.content_type == "voice"
    assert record.status == "processing"
    assert record.extra["duration_ms"] == 65000
    # 对象已搬到 voice/ 前缀，photos/ 旧键已删
    assert record.cos_key.startswith(f"voice/{user.id}/")
    assert backend.get_object(record.cos_key) == data
    with pytest.raises(KeyError):
        backend.get_object(result["file_key"])


def test_complete_voice_rejects_bad_duration(db_user):
    db, user = db_user
    task, data = _make_task(db, user)
    for i in range(task.chunk_count):
        upload_svc.upload_chunk(db, task.id, i, data[i * CHUNK : (i + 1) * CHUNK])
    result = upload_svc.complete_upload(db, task.id)
    with pytest.raises(ValueError):
        upload_svc.register_photo_content(
            db,
            user.id,
            result["file_key"],
            '{"content_type":"voice","duration_ms":"abc","source":"app"}',
        )

# ---------- P0 批次（2026-08-26）：幂等 / 魔数 / 存储兜底 ----------

def test_register_photo_content_idempotent(db_user):
    """P0-5（审查 H-5）：photo 分支幂等——同用户+同 cos_key → 返回既有记录（对齐 voice）

    complete 幂等 + 客户端重试 complete 后二次 register 不得产生重复内容。
    """
    db, user = db_user
    jpeg = _jpeg_bytes()
    key = f"photos/{user.id}/202608/dup_{uuid.uuid4().hex[:8]}.jpg"
    get_storage_backend().put_object(key, jpeg)
    cid1 = upload_svc.register_photo_content(
        db, user.id, key, '{"source":"app","upload_mode":"original"}'
    )
    cid2 = upload_svc.register_photo_content(
        db, user.id, key, '{"source":"app","upload_mode":"original"}'
    )
    assert cid1 == cid2
    records = db.query(Content).filter(
        Content.user_id == user.id, Content.cos_key == key
    ).all()
    assert len(records) == 1


def test_register_photo_content_idempotent_thumbnail_meta(db_user):
    """P0-5：thumbnail_meta 占位重试同样幂等（不重复建占位内容）"""
    db, user = db_user
    jpeg = _jpeg_bytes()
    key = f"photos/{user.id}/202608/th_dup_{uuid.uuid4().hex[:8]}.jpg"
    get_storage_backend().put_object(key, jpeg)
    cid1 = upload_svc.register_photo_content(
        db, user.id, key, '{"upload_mode":"thumbnail_meta","source":"app"}'
    )
    cid2 = upload_svc.register_photo_content(
        db, user.id, key, '{"upload_mode":"thumbnail_meta","source":"app"}'
    )
    assert cid1 == cid2
    records = db.query(Content).filter(
        Content.user_id == user.id, Content.cos_key == key
    ).all()
    assert len(records) == 1


def test_register_photo_content_enqueue_failure_still_returns(db_user, monkeypatch):
    """P0-5：enqueue 异常时捕获并返回成功（内容已建，管线可异步补投）

    F1/P0-6 双轨收口后入队统一在 services/photo_content（safe_enqueue_unique →
    enqueue_unique），monkeypatch 目标随之下沉到 photo_content。
    """
    db, user = db_user
    jpeg = _jpeg_bytes()
    key = f"photos/{user.id}/202608/enq_{uuid.uuid4().hex[:8]}.jpg"
    get_storage_backend().put_object(key, jpeg)

    def boom(*args, **kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr("app.services.photo_content.enqueue_unique", boom)
    cid = upload_svc.register_photo_content(
        db, user.id, key, '{"source":"app","upload_mode":"original"}'
    )
    record = db.get(Content, cid)
    assert record is not None
    assert record.status == "processing"
    assert record.cos_key == key


def test_register_photo_content_rejects_disguised_photo(db_user):
    """P0-3（审查 H3）：分片 complete 路径补魔数——伪装照片 → ValueError 且对象被清理"""
    db, user = db_user
    key = f"photos/{user.id}/202608/fake_{uuid.uuid4().hex[:8]}.jpg"
    get_storage_backend().put_object(key, b"<html>not-a-photo</html>")
    with pytest.raises(ValueError):
        upload_svc.register_photo_content(
            db, user.id, key, '{"source":"app","upload_mode":"original"}'
        )
    # best-effort 删除防孤儿（P0-3/P0-6）
    assert not get_storage_backend().object_exists(key)


def test_complete_upload_commit_failure_best_effort_delete(db_user, monkeypatch):
    """P0-6：complete 落对象后 commit 失败 → 尽力删除最终对象（防孤儿）"""
    db, user = db_user
    task, data = _make_task(db, user, data=_jpeg_bytes())
    for i in range(task.chunk_count):
        upload_svc.upload_chunk(db, task.id, i, data[i * CHUNK : (i + 1) * CHUNK])

    real_commit = db.commit

    def failing_commit():
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "commit", failing_commit)
    with pytest.raises(RuntimeError):
        upload_svc.complete_upload(db, task.id)
    monkeypatch.setattr(db, "commit", real_commit)
    assert not get_storage_backend().object_exists(task.file_key)


# ---------------------------------------------------------------------------
# H3：/upload/sts 生产门控 + user_id 透传（原 test_techdebt_p0.py P0-2 按域迁入）
# ---------------------------------------------------------------------------


def test_upload_sts_production_not_configured_501(client, auth_headers, monkeypatch):
    """P0-2：生产且 COS/STS 未真配 → 501 UPLOAD_008（不返回假凭证）"""
    _, headers = auth_headers("p0-sts-501")
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "tencent_secret_id", "")
    monkeypatch.setattr(settings, "tencent_secret_key", "")
    monkeypatch.setattr(settings, "cos_bucket", "")
    monkeypatch.setattr(settings, "tencent_sts_role_arn", "")
    r = client.get("/api/v1/upload/sts", headers=headers)
    assert r.status_code == 501
    assert r.json()["code"] == "UPLOAD_008"


def test_upload_sts_passes_user_id(client, auth_headers, monkeypatch):
    """P0-2：STS 凭证按请求方 user_id 签发（policy 路径级白名单的输入来源）"""
    import app.api.upload as upload_api

    _, headers = auth_headers("p0-sts-uid")
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "tencent_secret_id", "sid")
    monkeypatch.setattr(settings, "tencent_secret_key", "skey")
    monkeypatch.setattr(settings, "cos_bucket", "bucket")
    monkeypatch.setattr(settings, "cos_region", "ap-shanghai")
    monkeypatch.setattr(settings, "tencent_appid", "1250000000")
    monkeypatch.setattr(settings, "tencent_sts_role_arn", "arn:root")
    captured = {}

    class StubBackend:
        def get_sts_credentials(self, user_id=None):
            captured["user_id"] = user_id
            return {"tmp_secret_id": "s", "tmp_secret_key": "k", "session_token": "t"}

    def _stub_backend():
        return StubBackend()

    monkeypatch.setattr(upload_api, "get_storage_backend", _stub_backend)
    r = client.get("/api/v1/upload/sts", headers=headers)
    assert r.status_code == 200, r.text
    assert captured["user_id"], "必须把请求方 user_id 传入 STS 签发（禁止整桶凭证）"


def test_upload_sts_fake_backend_501(client, auth_headers):
    """P0-2：非 cos 后端 → 501 UPLOAD_005（既有语义回归）"""
    _, headers = auth_headers("p0-sts-fake")
    r = client.get("/api/v1/upload/sts", headers=headers)
    assert r.status_code == 501
    assert r.json()["code"] == "UPLOAD_005"
