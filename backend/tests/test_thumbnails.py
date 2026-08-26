"""缩略图管线测试（B4 · Wave3 AgentG · audit #1 缺口修复）

覆盖：
  - derive_thumbnail_key：原件键 → 确定性缩略图键
  - resize_to_jpeg：PIL 缩放为 JPEG、宽高比保持、非图片抛 ValueError
  - generate_thumbnail：photo + cos_key → 生成 + 回写 thumbnail_key + 幂等
  - 跳过语义：非 photo / 无 cos_key
  - get_thumbnail_bytes：归属校验（他人 → KeyError）+ 懒生成兜底
  - API 端点：GET /api/v1/thumbnails/{content_id} 返回 JPEG + 缓存头；越权 404
前置：PG yishu 库（fake 存储由 conftest autouse 强制）
"""
import io
import uuid

import pytest
from app.db.models import Content, User
from app.db.session import SessionLocal
from app.services import thumbnails
from app.services.external.storage import get_storage_backend
from PIL import Image
from sqlalchemy import delete as sa_delete

pytestmark = pytest.mark.integration


def _jpeg_bytes(width: int = 800, height: int = 600) -> bytes:
    img = Image.new("RGB", (width, height), (200, 120, 80))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture()
def db_user():
    db = SessionLocal()
    user = User(phone=f"thumb-test-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    db.execute(sa_delete(Content).where(Content.user_id == user.id))
    db.delete(user)
    db.commit()
    db.close()


def _make_photo(db, user, cos_key=None, data=None) -> Content:
    data = data if data is not None else _jpeg_bytes()
    key = cos_key or f"photos/{user.id}/202608/thumb_test_{uuid.uuid4().hex[:8]}.jpg"
    get_storage_backend().put_object(key, data)
    content = Content(
        user_id=user.id,
        content_type="photo",
        cos_key=key,
        source="app",
        status="processing",
    )
    db.add(content)
    db.commit()
    db.refresh(content)
    return content


def test_derive_thumbnail_key():
    key = thumbnails.derive_thumbnail_key("photos/u1/202608/abc123_x.jpg")
    assert key == "thumbnails/u1/202608/abc123_x.jpg"
    assert thumbnails.derive_thumbnail_key("wechat/u1/msg9.jpg") == "thumbnails/u1/msg9.jpg"
    # 确定性：同输入同输出
    assert thumbnails.derive_thumbnail_key("photos/u/1.jpg") == "thumbnails/u/1.jpg"


def test_resize_to_jpeg_keeps_aspect_and_smaller():
    original = _jpeg_bytes(1200, 900)
    thumb = thumbnails.resize_to_jpeg(original)
    img = Image.open(io.BytesIO(thumb))
    assert img.format == "JPEG"
    assert img.size == (480, 360)  # 长边压到 480，宽高比 4:3 保持
    assert len(thumb) < len(original)


def test_resize_to_jpeg_rejects_non_image():
    with pytest.raises(ValueError):
        thumbnails.resize_to_jpeg(b"this is not an image at all")


def test_generate_thumbnail_creates_and_idempotent(db_user):
    db, user = db_user
    content = _make_photo(db, user)
    r1 = thumbnails.generate_thumbnail(db, str(content.id))
    assert r1["status"] == "created"
    assert r1["thumbnail_key"] == thumbnails.derive_thumbnail_key(content.cos_key)
    db.refresh(content)
    assert content.thumbnail_key == r1["thumbnail_key"]
    assert get_storage_backend().object_exists(r1["thumbnail_key"])
    # 幂等：二次调用 → exists
    r2 = thumbnails.generate_thumbnail(db, str(content.id))
    assert r2["status"] == "exists"


def test_generate_thumbnail_skips_non_photo_and_no_original(db_user):
    db, user = db_user
    # 非 photo
    text = Content(user_id=user.id, content_type="text", text="x", source="app", status="done")
    db.add(text)
    db.commit()
    db.refresh(text)
    r = thumbnails.generate_thumbnail(db, str(text.id))
    assert r["status"] == "skipped"
    assert r["reason"] == thumbnails.SKIP_NOT_PHOTO
    # photo 但无 cos_key
    no_orig = Content(user_id=user.id, content_type="photo", source="app", status="done")
    db.add(no_orig)
    db.commit()
    db.refresh(no_orig)
    r2 = thumbnails.generate_thumbnail(db, str(no_orig.id))
    assert r2["status"] == "skipped"
    assert r2["reason"] == thumbnails.SKIP_NO_ORIGINAL


def test_get_thumbnail_bytes_ownership(db_user):
    """归属校验：他人内容 → KeyError（防 IDOR）"""
    db, user_a = db_user
    content = _make_photo(db, user_a)
    user_b = User(phone=f"thumb-attacker-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user_b)
    db.commit()
    db.refresh(user_b)
    try:
        with pytest.raises(KeyError):
            thumbnails.get_thumbnail_bytes(db, str(content.id), user_b.id)
    finally:
        db.delete(user_b)
        db.commit()


def test_get_thumbnail_bytes_lazy_generation(db_user):
    """懒生成兜底：无 thumbnail_key 的既有照片，GET 时按需生成"""
    db, user = db_user
    content = _make_photo(db, user)
    assert content.thumbnail_key is None
    data, content_type = thumbnails.get_thumbnail_bytes(db, str(content.id), user.id)
    assert content_type == "image/jpeg"
    assert data
    db.refresh(content)
    assert content.thumbnail_key is not None
    # 懒生成后对象已落
    assert get_storage_backend().object_exists(content.thumbnail_key)


def test_get_thumbnail_bytes_not_photo(db_user):
    db, user = db_user
    text = Content(user_id=user.id, content_type="text", text="x", source="app", status="done")
    db.add(text)
    db.commit()
    db.refresh(text)
    with pytest.raises(KeyError):
        thumbnails.get_thumbnail_bytes(db, str(text.id), user.id)


def test_thumbnail_api_endpoint(db_user):
    """API 冒烟：GET /api/v1/thumbnails/{content_id} 返回 JPEG + Cache-Control；越权 404"""
    from app.api import deps
    from app.api import thumbnails as thumbnails_api
    from app.main import app
    from fastapi.testclient import TestClient

    # 接线（集成 Agent 在 main.py 注册前，测试内手动挂载验证端点契约）
    app.include_router(thumbnails_api.router)
    db, user = db_user
    content = _make_photo(db, user)
    client = TestClient(app)

    def fake_user():
        return user

    app.dependency_overrides[deps.get_current_user] = fake_user
    try:
        r = client.get(f"/api/v1/thumbnails/{content.id}")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("image/jpeg")
        assert "max-age=86400" in r.headers.get("cache-control", "")

        # 越权（换用户）→ 404
        user_b = User(phone=f"thumb-api-{uuid.uuid4().hex[:8]}", status=1)
        db.add(user_b)
        db.commit()
        db.refresh(user_b)
        try:
            app.dependency_overrides[deps.get_current_user] = lambda: user_b
            r2 = client.get(f"/api/v1/thumbnails/{content.id}")
            assert r2.status_code == 404
        finally:
            db.delete(user_b)
            db.commit()
    finally:
        app.dependency_overrides.clear()
        # 移除测试挂载的路由，避免污染后续测试
        app.routes[:] = [
            route
            for route in app.routes
            if not getattr(route, "path", "").startswith("/api/v1/thumbnails")
        ]

# ---------- P0-3（审查 H3）：解压炸弹防护 ----------

def _png_bytes(width: int, height: int) -> bytes:
    """构造指定尺寸 PNG 头（无 IDAT；Image.open 只读头即可触发尺寸检查）"""
    import struct
    import zlib

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)

    def chunk(typ: bytes, data: bytes) -> bytes:
        body = typ + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    return sig + chunk(b"IHDR", ihdr) + chunk(b"IEND", b"")


def test_resize_to_jpeg_rejects_decompression_bomb():
    """P0-3：100k×100k 炸弹图（1e10 像素 >> 2×40MP）→ Image.open 抛
    DecompressionBombError → 包装为 ValueError，不崩溃不 OOM"""
    bomb = _png_bytes(100000, 100000)
    with pytest.raises(ValueError, match="解压炸弹"):
        thumbnails.resize_to_jpeg(bomb)


def test_resize_to_jpeg_rejects_oversized_dimensions():
    """P0-3：维度超 40MP 上限（MAX 与 2×MAX 之间只告警不抛）→ 显式校验拒绝"""
    big = _png_bytes(7000, 7000)  # 49MP > 40MP
    with pytest.raises(ValueError, match="图片尺寸超限"):
        thumbnails.resize_to_jpeg(big)


def test_resize_to_jpeg_normal_image_still_ok():
    """P0-3 防护不误伤：正常尺寸 JPEG 照常缩放"""
    original = _jpeg_bytes(1200, 900)
    thumb = thumbnails.resize_to_jpeg(original)
    img = Image.open(io.BytesIO(thumb))
    assert img.format == "JPEG"
    assert img.size == (480, 360)


def test_thumbnail_meta_bomb_rejected_cleanly(db_user):
    """P0-3：thumbnail_meta 分支同步解码——炸弹缩略图抛 ValueError（API 层 422），
    不炸请求线程（完整解码移 worker 为遗留登记项）"""
    db, user = db_user
    from app.services import upload as upload_svc

    bomb = _png_bytes(100000, 100000)
    key = f"photos/{user.id}/202608/bomb_{uuid.uuid4().hex[:8]}.jpg"
    get_storage_backend().put_object(key, bomb)
    with pytest.raises(ValueError):
        upload_svc.register_photo_content(
            db, user.id, key, '{"upload_mode":"thumbnail_meta","source":"app"}'
        )
