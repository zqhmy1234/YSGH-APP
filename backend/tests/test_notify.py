"""消息中心/推送测试（S4-07 推送 + S4-08 消息中心 + B5-c 情绪关怀）

覆盖：
  - create_message：统一入库（in-app / push 同表）+ mock 推送通道
  - generate_daily_review：有内容 → 复盘 push（按类型统计）；无内容 → 跳过
  - notify_voice_done：语音处理完成 push
  - maybe_send_emotion_care：情绪关怀分层触发（J-6，Wave4 AgentJ）
  - API：列表分页 / status 过滤 / 单条已读（幂等+越权 404）/ 全部已读
前置：PG yishu 库
"""
import uuid
from datetime import datetime

import pytest
from app.db.models import Content, Message, User
from app.db.session import SessionLocal
from app.services.notify import (
    REVIEW_TZ,
    create_message,
    generate_daily_review,
    maybe_notify_voice_done,
    maybe_send_emotion_care,
    notify_voice_done,
)
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

pytestmark = pytest.mark.integration


@pytest.fixture()
def db_user():
    db = SessionLocal()
    user = User(phone=f"msg-test-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    db.execute(sa_delete(Message).where(Message.user_id == user.id))
    db.execute(sa_delete(Content).where(Content.user_id == user.id))
    db.delete(user)
    db.commit()
    db.close()


def _content(db, user_id: str, ctype: str = "text", ts: datetime | None = None) -> Content:
    c = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type=ctype,
        text="测试内容",
        # 固定当天中午（而非 now-1h）：避免深夜 0:00-1:00 运行时跨天
        # 导致内容落在昨天、复盘统计今天 → 空结果（2026-08-20 实测复现）
        taken_at=ts or datetime.now(REVIEW_TZ).replace(hour=12, minute=0, second=0, microsecond=0),
        sensitive_status="正常",
        status="done",
    )
    db.add(c)
    db.commit()
    return c


def test_create_message_in_app_and_push(db_user):
    """统一入库：in-app 与 push 同表；push 走 mock 通道不抛错"""
    db, user = db_user
    m1 = create_message(db, user.id, "in_app", "care_followup", "想你了", "今天过得怎么样？", {"q": 1})
    m2 = create_message(db, user.id, "push", "daily_review", "今日回顾", "今天记了 2 条", {})
    assert m1.channel == "in_app" and m1.status == "unread"
    assert m2.channel == "push"
    rows = db.execute(select(Message).where(Message.user_id == user.id)).scalars().all()
    assert len(rows) == 2


def test_daily_review_generates_with_content(db_user):
    """22:00 复盘：有内容 → push 消息，按类型统计"""
    db, user = db_user
    _content(db, user.id, "text")
    _content(db, user.id, "photo")
    _content(db, user.id, "voice")
    msg = generate_daily_review(db, user.id)
    assert msg is not None
    assert msg.channel == "push" and msg.msg_type == "daily_review"
    assert msg.payload["stats"] == {"text": 1, "photo": 1, "voice": 1}
    assert "3 条" in msg.body
    local = msg.sent_at.astimezone(REVIEW_TZ)
    assert msg.title == f"{local.month}月{local.day}日 · 今日回顾"


def test_daily_review_skips_empty_day(db_user):
    """无内容 → 跳过（防打扰）"""
    db, user = db_user
    assert generate_daily_review(db, user.id) is None


def test_daily_review_skips_deleted_and_sensitive(db_user):
    """软删/敏感内容不计入复盘"""
    db, user = db_user
    c = _content(db, user.id, "text")
    c.sensitive_status = "敏感"
    db.commit()
    _content(db, user.id, "photo")
    c2 = _content(db, user.id, "voice")
    db.execute(sa_delete(Content).where(Content.id == c2.id))
    db.commit()
    msg = generate_daily_review(db, user.id)
    assert msg is not None
    assert msg.payload["stats"] == {"photo": 1}


def test_voice_done_notify(db_user):
    """语音处理完成 push"""
    db, user = db_user
    msg = notify_voice_done(db, user.id, "v-1")
    assert msg.channel == "push" and msg.msg_type == "voice_done"
    assert msg.payload["content_id"] == "v-1"


def test_messages_api_list_read(db_user):
    """API：列表分页 + status 过滤 + 单条已读 + 全部已读 + 越权 404"""
    from app.api import deps
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    create_message(db, user.id, "in_app", "care_followup", "标题A", "正文A")
    create_message(db, user.id, "in_app", "care_followup", "标题B", "正文B")
    other = User(phone=f"msg-oth-{uuid.uuid4().hex[:8]}", status=1)
    db.add(other)
    db.commit()
    create_message(db, other.id, "in_app", "care_followup", "别人的", "别人的消息")

    client = TestClient(app)
    app.dependency_overrides[deps.get_current_user] = lambda: user
    try:
        r = client.get("/api/v1/messages")
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data["has_more"] is False and len(data["items"]) == 2
        assert all(i["status"] == "unread" for i in data["items"])

        # status 过滤
        r2 = client.get("/api/v1/messages", params={"status": "read"})
        assert r2.json()["data"]["items"] == []

        # 单条已读
        mid = data["items"][0]["id"]
        r3 = client.post(f"/api/v1/messages/{mid}/read")
        assert r3.status_code == 200
        r4 = client.get("/api/v1/messages")
        unread = [i for i in r4.json()["data"]["items"] if i["status"] == "unread"]
        assert len(unread) == 1

        # 已读幂等
        r5 = client.post(f"/api/v1/messages/{mid}/read")
        assert r5.status_code == 200

        # 越权：读他人消息 → 404（不泄露存在性）
        oid = db.execute(
            select(Message.id).where(Message.user_id == other.id)
        ).scalar()
        r6 = client.post(f"/api/v1/messages/{oid}/read")
        assert r6.status_code == 404

        # 全部已读
        r7 = client.post("/api/v1/messages/read-all")
        assert r7.status_code == 200
        r8 = client.get("/api/v1/messages", params={"status": "unread"})
        assert r8.json()["data"]["items"] == []
    finally:
        app.dependency_overrides.clear()
        db.execute(sa_delete(Message).where(Message.user_id == other.id))
        db.delete(other)
        db.commit()


# ---------- B5-c 情绪关怀分层触发（J-6 · Wave4 AgentJ）----------


def _voice_content(db, user_id: str, emotion: str, confidence: float, text: str = "今天好累") -> Content:
    ts = datetime.now(REVIEW_TZ).replace(hour=12, minute=0, second=0, microsecond=0)
    c = _content(db, user_id, ctype="voice", ts=ts)
    c.emotion = {
        "emotion": emotion,
        "confidence": confidence,
        "source": "sensevoice_local",
        "model": "iic/SenseVoiceSmall-onnx",
        "actionable": emotion != "平静" and confidence >= 0.7,
    }
    c.text = text
    db.commit()
    return c


def test_care_gate_below_threshold_not_triggered(db_user):
    """J-6 门控：confidence < 0.7 不触发（只存档案不打扰）"""
    db, user = db_user
    c = _voice_content(db, user.id, "难过", 0.5, "没什么")
    assert maybe_send_emotion_care(db, c) is None
    c2 = _voice_content(db, user.id, "平静", 0.9, "今天不错")
    assert maybe_send_emotion_care(db, c2) is None


def test_care_sad_without_reason_asks(db_user):
    """J-6：SAD + 未说明原因 → 关怀追问"""
    db, user = db_user
    c = _voice_content(db, user.id, "难过", 0.9, "唉")
    msg = maybe_send_emotion_care(db, c)
    assert msg is not None
    assert msg.msg_type == "care_followup"
    assert msg.channel == "in_app"
    assert msg.payload["template"] == "sad_ask"
    assert "怎么啦" in msg.body


def test_care_sad_with_reason_responds(db_user):
    """J-6：SAD + 已说明原因 → 回应内容而非追问（再问是废话）"""
    db, user = db_user
    c = _voice_content(db, user.id, "难过", 0.9, "今天工作太忙太累了")
    msg = maybe_send_emotion_care(db, c)
    assert msg is not None
    assert msg.payload["template"] == "sad_respond"
    assert "辛苦" in msg.body


def test_care_angry_companion_exit(db_user):
    """J-6：ANGRY → 陪伴出口，不主动追问（愤怒时关怀是火上浇油）"""
    db, user = db_user
    c = _voice_content(db, user.id, "生气", 0.9, "气死我了")
    msg = maybe_send_emotion_care(db, c)
    assert msg is not None
    assert msg.payload["template"] == "angry"
    assert "随时找我" in msg.body or "随时找我" in msg.title


def test_care_late_night_lightweight(db_user, monkeypatch):
    """J-6：深夜时段（23:00）→ 轻量表达，不催回复"""
    import app.services.notify as notify_mod

    class FakeDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 8, 26, 23, 0, 0, tzinfo=REVIEW_TZ)

    monkeypatch.setattr(notify_mod, "datetime", FakeDatetime)
    db, user = db_user
    c = _voice_content(db, user.id, "难过", 0.9, "唉")
    msg = maybe_send_emotion_care(db, c)
    assert msg is not None
    assert msg.payload["template"] == "late_night"


def test_care_frequency_decay(db_user):
    """J-6：连续多日负面 → 频次递减（第 1 天问 → 第 2 天好些了吗 → 第 3 天只陪伴）"""
    db, user = db_user
    c = _voice_content(db, user.id, "难过", 0.9, "唉")

    msg1 = maybe_send_emotion_care(db, c)
    assert msg1.payload["template"] == "sad_ask"

    msg2 = maybe_send_emotion_care(db, c)
    assert msg2.payload["template"] == "day2"

    msg3 = maybe_send_emotion_care(db, c)
    assert msg3.payload["template"] == "day3"

    msg4 = maybe_send_emotion_care(db, c)
    assert msg4.payload["template"] == "day3"


def test_care_other_negative_default_companion(db_user):
    """J-6：其它负面（恐惧/厌恶）→ 默认陪伴出口（保守不追问）"""
    db, user = db_user
    c = _voice_content(db, user.id, "恐惧", 0.9, "好怕")
    msg = maybe_send_emotion_care(db, c)
    assert msg is not None
    assert msg.payload["template"] == "angry"


def test_maybe_notify_voice_done(db_user):
    """J-6：voice_done 接线 —— 语音有文本 → push；空白/非语音 → None"""
    db, user = db_user
    c = _voice_content(db, user.id, "平静", 0.0, "今天天气不错")
    msg = maybe_notify_voice_done(db, c)
    assert msg is not None
    assert msg.msg_type == "voice_done"
    assert msg.channel == "push"

    c.text = "   "
    db.commit()
    assert maybe_notify_voice_done(db, c) is None

    c2 = _content(
        db,
        user.id,
        ctype="text",
        ts=datetime.now(REVIEW_TZ).replace(hour=12, minute=0, second=0, microsecond=0),
    )
    assert maybe_notify_voice_done(db, c2) is None
