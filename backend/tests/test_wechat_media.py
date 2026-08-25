"""微信媒体云端原件测试（B4 · Wave3 AgentG · audit #8 缺口修复）

覆盖（fake 存储 + mock 媒体字节）：
  - image → 下载 → COS 落库（Content.cos_key）→ 入管线（photo，source=wechat）+ 追溯 extra
  - voice → 同上（voice 内容 + file_name）
  - 未绑定用户（user_id=None）→ 只记 wechat_messages，不下载媒体
  - 下载失败 → wechat_messages.status=media_failed，不建内容
  - 图片 CI 审核命中（敏感排除）→ 不进云端镜像（不建 content）
  - msg_id 幂等（媒体消息重复回调只入一次）
前置：PG yishu 库（fake 存储由 conftest autouse 强制；Redis 提供 enqueue）
"""
import uuid

import pytest
from app.db.models import Content, User, WechatMessage
from app.db.session import SessionLocal
from app.services.external.storage import get_storage_backend
from app.services.wechat.service import process_incoming
from sqlalchemy import delete as sa_delete

pytestmark = pytest.mark.integration


@pytest.fixture()
def db_user():
    db = SessionLocal()
    user = User(phone=f"wxmedia-test-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    db.execute(sa_delete(Content).where(
        (Content.user_id == user.id) & (Content.source == "wechat")
    ))
    db.execute(sa_delete(WechatMessage).where(
        WechatMessage.msg_id.like(f"wxm-{user.id}-%")
    ))
    db.delete(user)
    db.commit()
    db.close()


def _media_msg(user, msg_type: str, media_id: str) -> dict:
    return {
        "msg_id": f"wxm-{user.id}-{uuid.uuid4().hex[:8]}",
        "msg_type": msg_type,
        "media_id": media_id,
    }


def test_image_media_downloads_to_storage_and_content(db_user):
    """image → 下载（mock）→ COS 落 Content.cos_key → 入管线（photo）"""
    db, user = db_user
    msg = _media_msg(user, "image", f"MEDIA_IMG-{uuid.uuid4().hex[:8]}")
    result = process_incoming(db, msg, user_id=user.id)

    assert result["status"] == "created"
    assert result["media"] == "ok"
    assert result["content_id"]

    row = db.query(WechatMessage).filter(WechatMessage.msg_id == msg["msg_id"]).one()
    assert row.status == "processed"
    assert row.media_id == msg["media_id"]

    content = db.get(Content, result["content_id"])
    assert content.content_type == "photo"
    assert content.source == "wechat"
    assert content.cos_key == f"wechat/{user.id}/{msg['msg_id']}.jpg"
    assert content.status == "processing"
    # 追溯 extra（无需新列）
    assert content.extra["wechat_msg_id"] == msg["msg_id"]
    assert content.extra["wechat_media_id"] == msg["media_id"]
    # mock 媒体字节已落 COS
    assert get_storage_backend().object_exists(content.cos_key)


def test_voice_media_downloads_to_storage_and_content(db_user):
    """voice → 下载 → COS 落 Content.cos_key → voice 内容 + file_name"""
    db, user = db_user
    msg = _media_msg(user, "voice", f"MEDIA_VO-{uuid.uuid4().hex[:8]}")
    result = process_incoming(db, msg, user_id=user.id)
    content = db.get(Content, result["content_id"])
    assert content.content_type == "voice"
    assert content.cos_key == f"wechat/{user.id}/{msg['msg_id']}.amr"
    assert content.extra["file_name"] == f"{msg['media_id']}.amr"
    assert get_storage_backend().object_exists(content.cos_key)


def test_media_unbound_user_only_records_message(db_user):
    """未绑定 unionid → 只记 wechat_messages，不下载媒体/不建内容"""
    db, user = db_user
    msg = _media_msg(user, "image", "MEDIA_UNBOUND")
    result = process_incoming(db, msg, user_id=None)
    assert result["status"] == "created"
    assert "media" not in result
    row = db.query(WechatMessage).filter(WechatMessage.msg_id == msg["msg_id"]).one()
    assert row.user_id is None
    contents = db.query(Content).filter(Content.source == "wechat").all()
    assert contents == []


def test_media_download_failure_marks_message(db_user, monkeypatch):
    """下载失败 → media_failed 标记，不建内容"""
    db, user = db_user
    from app.services.wechat import service as wx

    def _boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(wx, "download_media", _boom)
    msg = _media_msg(user, "image", "MEDIA_FAIL")
    result = process_incoming(db, msg, user_id=user.id)
    assert result["media"] == "failed"
    row = db.query(WechatMessage).filter(WechatMessage.msg_id == msg["msg_id"]).one()
    assert row.status == "media_failed"
    contents = db.query(Content).filter(Content.source == "wechat").all()
    assert contents == []


def test_image_sensitive_excluded_from_cloud(db_user, monkeypatch):
    """图片 CI 审核命中（敏感排除 S4-03）→ 不进云端镜像（不建 content）"""
    db, user = db_user
    from app.services.wechat import service as wx

    monkeypatch.setattr(wx, "_audit_image", lambda cos_key: {"pass": False, "labels": ["PornInfo:xxx"]})
    msg = _media_msg(user, "image", "MEDIA_SENSITIVE")
    result = process_incoming(db, msg, user_id=user.id)
    assert result["media"] == "blocked"
    assert result["sensitive"] is True
    row = db.query(WechatMessage).filter(WechatMessage.msg_id == msg["msg_id"]).one()
    assert row.status == "sensitive"
    contents = db.query(Content).filter(Content.source == "wechat").all()
    assert contents == []


def test_media_msg_id_idempotent(db_user):
    """媒体消息重复回调 → duplicate，只入一次"""
    db, user = db_user
    msg = _media_msg(user, "image", "MEDIA_DUP")
    r1 = process_incoming(db, msg, user_id=user.id)
    r2 = process_incoming(db, msg, user_id=user.id)
    assert r1["status"] == "created"
    assert r2["status"] == "duplicate"
    rows = db.query(WechatMessage).filter(WechatMessage.msg_id == msg["msg_id"]).all()
    assert len(rows) == 1
    contents = db.query(Content).filter(Content.source == "wechat").all()
    assert len(contents) == 1  # 只建一次
