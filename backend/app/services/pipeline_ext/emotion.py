"""emotion.py —— B5a 语音域钩子：情绪落库后消费

任务归属：Wave 4 Agent J（B5a 客户端/消费域）独占本文件。
实现（J-5 + J-6 数据/通知联动）：
- 语音情绪（PR#1 已实现：emotion/confidence/source/actionable）联动事件层
- events.emotion 写入（模型字段已存在，现零写入）：主导+峰值结构（对齐 B5a §3）
- 情绪关怀触发所需的情绪事件投递（触发逻辑在 notify.py，本钩子负责接线）

接线说明（2026-08-26 集成 Agent 需要补一行，pipeline.py 冻结不可改）：
  enrich_content_emotion（pipeline.py:233）完成本地情绪增强后，目前不会重新调用
  本钩子 → 初始处理时 funasr 通道 emotion 恒"平静"（enrich 才产出真情绪），
  关怀触发/事件层联动会滞后。集成 Agent 在 enrich_content_emotion 的 content.emotion
  赋值后（约 pipeline.py:297 db.commit() 前）补：
      from app.services.pipeline_ext import consume_emotion
      consume_emotion(db, content)
  本钩子幂等安全（重复调用只刷新 events.emotion；关怀/voice_done 由 msg_type
  幂等键控制），补行后无需改本文件。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.db.models import Content

logger = logging.getLogger("yishu.pipeline_ext.emotion")

# 与 asr.EMOTION_ACTION_THRESHOLD 对齐（避免循环 import，本地常量）
EMOTION_ACTION_THRESHOLD = 0.7


def _emotion_merge_from_content(content: Content) -> dict | None:
    """把 content.emotion 归一化为事件层 dominant/peak 结构（对齐 B5a §3）。

    平静/无情绪不联动事件层（不覆盖事件既有的真实情绪标记）。
    """
    emo = content.emotion or {}
    emotion = str(emo.get("emotion") or "平静")
    if emotion == "平静":
        return None
    confidence = float(emo.get("confidence") or 0.0)
    source = str(emo.get("source") or "none")
    return {
        "emotion": emotion,
        "confidence": confidence,
        "source": source,
        "dominant": {
            "emotion": emotion,
            "confidence": confidence,
            "source": source,
        },
        "peak": {
            "emotion": emotion,
            "confidence": confidence,
            "source": source,
        },
        "actionable": bool(emo.get("actionable")),
    }


def _link_events_emotion(db: Session, content: Content) -> None:
    """语音内容情绪写入其所属事件 events.emotion（主导+峰值，J-5）。

    只联动非软删事件；调用方（pipeline）在统一事务末尾 commit，
    本钩子只做内存改动不提交（避免嵌套事务）。
    """
    if content.content_type != "voice":
        return
    merge = _emotion_merge_from_content(content)
    if merge is None:
        return

    from app.db.models import Event, EventItem

    events = list(
        db.scalars(
            select(Event)
            .join(EventItem, EventItem.event_id == Event.id)
            .where(
                EventItem.content_id == content.id,
                Event.deleted_at.is_(None),
            )
        )
    )
    for event in events:
        current = dict(event.emotion or {})
        current.update(merge)  # 主导+峰值+来源+actionable（保持历史字段不丢）
        event.emotion = current
    if events:
        logger.info("events.emotion 联动 content=%s events=%d", content.id, len(events))


def consume_emotion(db: Session, content: Content) -> None:
    """情绪落库后消费：① events.emotion 联动（J-5）② 关怀/voice_done 接线（J-6）。

    幂等安全：events.emotion 刷新幂等；通知由 msg_type（voice_done/care_followup）
    语义保证可重复触发（产品侧以消息中心自然去重）。
    """
    # ① 事件层联动
    try:
        _link_events_emotion(db, content)
    except Exception:  # noqa: BLE001 —— 事件层联动失败不阻断主流程
        logger.exception("events.emotion 联动失败 content=%s", content.id)

    # ② 通知接线：voice_done + 情绪关怀（仅语音、处理完成、有文本时）
    try:
        from app.services.notify import maybe_notify_voice_done

        maybe_notify_voice_done(db, content)
    except Exception:  # noqa: BLE001 —— 通知失败不阻断主流程
        logger.exception("voice_done 通知失败 content=%s", content.id)

    try:
        from app.services.notify import maybe_send_emotion_care

        maybe_send_emotion_care(db, content)
    except Exception:  # noqa: BLE001 —— 关怀触发失败不阻断主流程
        logger.exception("情绪关怀触发失败 content=%s", content.id)
