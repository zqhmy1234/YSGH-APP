"""B5a 情绪消费钩子测试（J-5 · Wave4 AgentJ）：events.emotion 联动 + 通知接线

覆盖：
  - consume_emotion：语音内容情绪写入其所属事件 events.emotion（主导+峰值）
  - 非语音 / 平静情绪 / 无关联事件 → 不联动（幂等安全）
  - 通知接线：voice_done + 关怀消息生成（不重复提交问题由 pipeline 事务处理）

前置：PG yishu 库
"""
import uuid
from datetime import datetime

import pytest
from app.db.models import Content, Event, EventItem, Message
from app.services.notify import REVIEW_TZ
from app.services.pipeline_ext.emotion import consume_emotion

pytestmark = pytest.mark.integration


def _voice(db, user_id: str, emotion: str, confidence: float, text: str) -> Content:
    c = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type="voice",
        text=text,
        taken_at=datetime.now(REVIEW_TZ).replace(hour=12, minute=0, second=0, microsecond=0),
        emotion={
            "emotion": emotion,
            "confidence": confidence,
            "source": "sensevoice_local",
            "model": "iic/SenseVoiceSmall-onnx",
            "actionable": emotion != "平静" and confidence >= 0.7,
        },
        sensitive_status="正常",
        status="done",
    )
    db.add(c)
    db.commit()
    return c


def _link_event(db, user_id: str, content_id: str, title: str = "事件") -> Event:
    ev = Event(
        id=str(uuid.uuid4()),
        user_id=user_id,
        level=1,
        title=title,
        status="draft",
    )
    db.add(ev)
    db.flush()
    db.add(EventItem(content_id=content_id, event_id=ev.id))
    db.commit()
    return ev


def test_consume_emotion_writes_events_dominant_peak(db_user):
    """J-5：语音情绪写入 events.emotion（主导+峰值结构）"""
    db, user = db_user
    c = _voice(db, user.id, "难过", 0.92, "今天工作好累")
    ev = _link_event(db, user.id, c.id)

    consume_emotion(db, c)
    # R2#2（事务边界）：consume_emotion 全链不再 commit，events.emotion 为内存
    # 变更——显式 commit 后 refresh 才能读到（落库由最外层编排者统一 commit）
    db.commit()
    db.refresh(ev)

    assert ev.emotion is not None
    assert ev.emotion["emotion"] == "难过"
    assert ev.emotion["confidence"] == 0.92
    assert ev.emotion["source"] == "sensevoice_local"
    assert ev.emotion["dominant"]["emotion"] == "难过"
    assert ev.emotion["peak"]["emotion"] == "难过"
    assert ev.emotion["actionable"] is True


def test_consume_emotion_skips_non_voice_and_neutral(db_user):
    """J-5：非语音 / 平静情绪 → 不联动事件层"""
    db, user = db_user

    # 非语音（text 类型带情绪字段也不联动）
    t = Content(
        id=str(uuid.uuid4()),
        user_id=user.id,
        content_type="text",
        text="文字内容",
        emotion={"emotion": "难过", "confidence": 0.9, "source": "x"},
        sensitive_status="正常",
        status="done",
    )
    db.add(t)
    db.commit()
    ev_t = _link_event(db, user.id, t.id)
    consume_emotion(db, t)
    db.refresh(ev_t)
    assert ev_t.emotion is None

    # 平静情绪不联动
    c = _voice(db, user.id, "平静", 0.9, "今天不错")
    ev_c = _link_event(db, user.id, c.id)
    consume_emotion(db, c)
    db.refresh(ev_c)
    assert ev_c.emotion is None


def test_consume_emotion_links_no_event_noop(db_user):
    """J-5：语音内容无关联事件 → 不报错、不产生事件"""
    db, user = db_user
    c = _voice(db, user.id, "难过", 0.9, "唉")
    consume_emotion(db, c)  # 不应抛异常
    from sqlalchemy import select

    evs = db.scalars(select(Event).where(Event.user_id == user.id)).all()
    assert evs == []


def test_consume_emotion_fires_notifications(db_user):
    """J-5/J-6 接线：consume_emotion 触发 voice_done push + 关怀 in-app"""
    db, user = db_user
    c = _voice(db, user.id, "难过", 0.9, "唉")
    _link_event(db, user.id, c.id)
    consume_emotion(db, c)

    msgs = db.query(Message).filter(Message.user_id == user.id).all()
    types = {m.msg_type for m in msgs}
    assert "voice_done" in types
    assert "care_followup" in types
    care = [m for m in msgs if m.msg_type == "care_followup"][0]
    assert care.channel == "in_app"
    assert care.payload["emotion"] == "难过"
