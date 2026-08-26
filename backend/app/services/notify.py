"""消息通知服务（S4-07 推送 + S4-08 消息中心 + B5-c 情绪关怀）

- create_message：统一消息入库（in-app 与 push 同表 messages）
- 推送通道：推送厂商凭证未配置 → mock 通道（日志占位 MOCK_PUSH）；
  配置后零切换（channel == push 时走真实厂商，见 TODO(T1)）
- generate_daily_review：22:00 每日复盘（产品部推送策略：复盘走 push）；
  无内容用户跳过（防打扰）
- notify_voice_done：语音处理完成 push（S4-07 第二类 push）
- maybe_send_emotion_care：情绪关怀分层触发（B5-c，Wave4 AgentJ 实现）：
  SAD 未说明原因→关怀追问 / SAD 已说明→回应内容 / ANGRY→陪伴出口 /
  深夜轻量 / 连续多日频次递减；<0.7 不触发；文案库占位（产品部提供后替换）

关怀文案库：CARE_TEMPLATES 为产品部文案占位（2026-08-26），产品部提供正式
文案后仅替换模板内容，触发逻辑不变。

22:00 复盘调度登记（2026-08-26，集成 Agent 部署侧挂定时）：
  RQ 无内置 cron —— 用 rq-scheduler / APScheduler / 系统 cron / Windows 计划任务
  每天本地 22:00 调一次：python backend/scripts/daily_review.py
  （脚本幂等：重复跑生成新消息不覆盖；无内容用户自动跳过防打扰）
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Content, Message

logger = logging.getLogger("yishu.notify")

# 复盘生成时区（产品口径：本地 22:00；MVP 中国区固定 +08:00）
REVIEW_TZ = timezone(timedelta(hours=8))

# 内容类型中文（复盘文案用）
_TYPE_CN = {
    "photo": "照片",
    "text": "文字",
    "voice": "语音",
    "article": "文章",
}

# ---- 情绪关怀（B5-c · Wave4 AgentJ）----
EMOTION_ACTION_THRESHOLD = 0.7   # <0.7 不触发（设计口径：只存档案不参与触发）
LATE_NIGHT_START_HOUR = 22       # 深夜轻量：22:00-05:00
LATE_NIGHT_END_HOUR = 5
CARE_STREAK_LOOKBACK_DAYS = 3    # 连续多日频次递减回看窗口

# 关怀文案库（产品部占位，2026-08-26；产品部提供正式文案后仅替换模板）
CARE_TEMPLATES: dict[str, dict[str, str]] = {
    "sad_ask": {
        "title": "想陪你聊聊",
        "body": "刚刚听到你好像有点心事。今天怎么啦？我随时都在。",
    },
    "sad_respond": {
        "title": "辛苦了",
        "body": "听到你说的这些，真的辛苦了。想找人说说，我都在。",
    },
    "angry": {
        "title": "我在",
        "body": "听起来今天有点气。不急着说，想聊的时候随时找我。",
    },
    "late_night": {
        "title": "夜深了",
        "body": "如果还醒着，我随时在。别一个人扛着。",
    },
    "day2": {
        "title": "好些了吗",
        "body": "今天感觉好些了吗？还记得我。",
    },
    "day3": {
        "title": "陪你",
        "body": "这几天都有点低落吧。不用说什么，我就在这儿陪你。",
    },
}

# 已说明原因的启发式标记（产品部提供正式语义模型前用；命中 → 回应内容而非追问）
_SAD_REASON_MARKERS = (
    "累", "忙", "工作", "加班", "考试", "压力", "难受", "生病",
    "吵架", "分手", "失败", "失业", "被骂", "太累",
)


def _explained_reason(text: str | None) -> bool:
    """SAD 是否已说明原因（轻量占位：文本含负面归因词 → 已说明）。"""
    if not text:
        return False
    return any(marker in text for marker in _SAD_REASON_MARKERS)


def _is_late_night(now: datetime | None = None) -> bool:
    """深夜时段（本地 22:00-05:00）→ 轻量表达不催回复。"""
    current = (now or datetime.now(REVIEW_TZ)).astimezone(REVIEW_TZ)
    return current.hour >= LATE_NIGHT_START_HOUR or current.hour < LATE_NIGHT_END_HOUR


def _care_streak_days(db: Session, user_id: str, now: datetime | None = None) -> int:
    """近 N 天已触发的关怀消息数（频次递减输入；同日多条算多条，保守节流）。

    只取下界（lookback 窗口）：上界 sent_at <= now 依赖客户端时间与 DB 时钟一致，
    测试/时钟偏差场景会漏计数（2026-08-26 集成修复）；未来时间消息不应参与节流。
    """
    current = now or datetime.now(REVIEW_TZ)
    start = current - timedelta(days=CARE_STREAK_LOOKBACK_DAYS)
    return int(
        db.scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.user_id == user_id,
                Message.msg_type == "care_followup",
                Message.sent_at >= start,
            )
        )
        or 0
    )


def create_message(
    db: Session,
    user_id: str,
    channel: str,
    msg_type: str,
    title: str,
    body: str,
    payload: dict | None = None,
) -> Message:
    """统一消息入库（in-app 与 push 同表）；push 消息经 mock 通道发送

    R2#2（事务边界）：本函数**不 commit**——只 db.flush([msg]) 取得消息 id
    （供 mock 推送日志），落库由最外层编排者统一 commit：
      管线 process_content / 情绪任务 enrich_content_emotion / 复盘 generate_daily_review。
    此前 create_message 内 db.commit() 被管线事务调用（consume_emotion →
    maybe_send_emotion_care）时嵌套 commit，破坏主事务原子性（重构侦察 R2-P1#2）。
    用 flush([msg]) 仅刷本条消息，不触发会话整体 flush（不把内容行变更提前落库）。
    """
    msg = Message(
        user_id=user_id,
        channel=channel,
        msg_type=msg_type,
        title=title,
        body=body,
        payload=payload or {},
    )
    db.add(msg)
    db.flush([msg])

    if channel == "push":
        # 推送厂商凭证未配置 → mock 通道（S4-07：交付调度+消息生成+消息中心；
        # 凭证到位后在此接入真实厂商，幂等键 = messages.id）
        logger.info("[MOCK_PUSH] user=%s msg_id=%s title=%s body=%s", user_id, msg.id, title, body)
    return msg


def _day_range(day: date) -> tuple[datetime, datetime]:
    """本地日界 [day 00:00, day+1 00:00)（复盘按本地日界统计）"""
    start = datetime.combine(day, time.min, tzinfo=REVIEW_TZ)
    return start, start + timedelta(days=1)


def _today_stats(db: Session, user_id: str, day: date) -> dict[str, int]:
    """今日内容统计（按 content_type；只计非软删、非敏感）"""
    start, end = _day_range(day)
    rows = db.execute(
        select(Content.content_type, func.count())
        .where(
            Content.user_id == user_id,
            Content.deleted_at.is_(None),
            or_(Content.sensitive_status.is_(None), Content.sensitive_status == "正常"),
            Content.taken_at >= start,
            Content.taken_at < end,
        )
        .group_by(Content.content_type)
    ).all()
    return {t: n for t, n in rows}


def generate_daily_review(db: Session, user_id: str, day: date | None = None) -> Message | None:
    """每日复盘（22:00 push）：汇总今日内容；无内容返回 None（防打扰）

    R2#2（事务边界）：本函数是每日复盘独立流程的最外层编排者（daily_review.py
    每用户调用一次，不再依赖 create_message 内部 commit）——生成消息后统一
    commit 落库。只在独立脚本流程调用，不会被嵌入其它事务。
    """
    day = day or datetime.now(REVIEW_TZ).date()
    stats = _today_stats(db, user_id, day)
    if not stats:
        logger.info("user=%s 今日无内容，跳过复盘", user_id)
        return None

    total = sum(stats.values())
    parts = "、".join(f"{_TYPE_CN.get(t, t)} {n} 条" for t, n in sorted(stats.items()))
    msg = create_message(
        db,
        user_id,
        channel="push",
        msg_type="daily_review",
        title=f"{day.month}月{day.day}日 · 今日回顾",
        body=f"今天记下了 {total} 条记忆（{parts}）。睡前花一分钟看看，让日子被记住。",
        payload={"day": day.isoformat(), "stats": stats, "template": "mock"},
    )
    db.commit()
    return msg


def notify_voice_done(db: Session, user_id: str, content_id: str) -> Message:
    """语音处理完成 push（S4-07：语音异步转写完成后通知）"""
    return create_message(
        db,
        user_id,
        channel="push",
        msg_type="voice_done",
        title="语音已整理好",
        body="你刚刚的语音已经整理完成，可以来看看。",
        payload={"content_id": content_id, "template": "mock"},
    )


def maybe_notify_voice_done(db: Session, content: Content) -> Message | None:
    """voice_done 接线（J-6）：语音处理完成且已产生可展示文本 → push。

    由 pipeline_ext.emotion.consume_emotion 调用（pipeline.py 冻结不可改）；
    空白语音（no_speech）/ 无文本不打扰。
    """
    if content.content_type != "voice":
        return None
    if not (content.text or "").strip():
        return None
    return notify_voice_done(db, content.user_id, str(content.id))


def maybe_send_emotion_care(db: Session, content: Content) -> Message | None:
    """情绪关怀分层触发（B5-c · J-6，对齐 B5a §4 输入分布分层）。

    规则：
      - 门控：emotion==平静 或 confidence < 0.7 → 不触发（只存档案不打扰）
      - SAD + 未说明原因 → 关怀追问（"今天怎么啦"）
      - SAD + 已说明原因 → 回应内容（"辛苦了"，再问是废话）
      - ANGRY → 不主动追问，提供陪伴出口（愤怒时关怀是火上浇油）
      - 深夜（22:00-05:00）→ 轻量表达，不催回复
      - 连续多日负面 → 频次递减：第 1 天问 → 第 2 天"好些了吗" → 第 3 天起只陪伴
      - 其他负面（恐惧/厌恶/惊讶）→ 默认陪伴出口（保守不追问）
    返回生成的 Message；未触发返回 None。文案用 CARE_TEMPLATES 占位。
    """
    emo = content.emotion or {}
    emotion = str(emo.get("emotion") or "平静")
    confidence = float(emo.get("confidence") or 0.0)
    if emotion == "平静" or confidence < EMOTION_ACTION_THRESHOLD:
        return None

    template_key = "angry"  # 默认陪伴出口（保守）
    if emotion == "生气":
        template_key = "angry"
    elif emotion == "难过":
        now = datetime.now(REVIEW_TZ)
        if _is_late_night(now):
            template_key = "late_night"
        elif _explained_reason(content.text):
            template_key = "sad_respond"
        else:
            streak = _care_streak_days(db, content.user_id, now)
            if streak >= 2:
                template_key = "day3"
            elif streak >= 1:
                template_key = "day2"
            else:
                template_key = "sad_ask"
    # 恐惧/厌恶/惊讶等其它负面 → 保持默认陪伴出口（不追问原因）

    tpl = CARE_TEMPLATES[template_key]
    return create_message(
        db,
        content.user_id,
        channel="in_app",
        msg_type="care_followup",
        title=tpl["title"],
        body=tpl["body"],
        payload={
            "content_id": str(content.id),
            "emotion": emotion,
            "confidence": confidence,
            "template": template_key,
        },
    )
