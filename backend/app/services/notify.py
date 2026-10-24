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
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Protocol, runtime_checkable

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Content, Message

logger = logging.getLogger("yishu.notify")


# ---------------------------------------------------------------------------
# 推送通道端口（R1#12：抽象推送通道，mock/真实通道实现，行为等价）
#
# create_message 的 push 分支经端口投递（幂等键 = message.id）；默认 MockPushChannel
# 日志占位（S4-07：交付调度+消息生成+消息中心），凭证到位后 set_push_channel 换真实
# 厂商通道零切换。
#
# B-02（2026-10-24 收口）：`set_push_channel()` 此前**零调用**（定义后全仓无调用方），
# 故生产恒走 MockPushChannel ⇒ 消息真实落库、手机却**不会有任何系统通知**
# （用户只能自己打开 App 进消息页才看到；真库 589 条消息里 491 条未读）。
# 现补：① 真实可用通道 `WebhookPushChannel`（配 URL 即真 POST，未配置**显式告警 + 计数**，
# 绝不伪装成功）；② `main.py` lifespan 用 `set_push_channel()` 接线。
# 厂商通道（uni-push / 华为 / 小米）仍需 AppID+证书，本仓一个都没到位，故本批**不接 SDK**。
# ---------------------------------------------------------------------------
@runtime_checkable
class PushChannel(Protocol):
    """推送通道端口：把已入库消息投递给推送厂商"""

    def send(self, *, user_id: str, message: Message) -> None:
        """推送一条消息（幂等键 = message.id；实现方负责真实投递，失败记日志不抛）"""
        ...


class MockPushChannel:
    """mock 通道：日志占位（凭证未配置时的默认实现；配 key 后换真实通道）"""

    def send(self, *, user_id: str, message: Message) -> None:
        logger.info("[MOCK_PUSH] user=%s msg_id=%s title=%s body=%s", user_id, message.id, message.title, message.body)


# ---- 投递计数（B-02：未配置/失败必须**可观测**，不静默）----


@dataclass
class PushDeliveryStats:
    """推送投递计数（进程级，供运维/测试断言"到底推没推出去"）

    为什么需要：本缺陷的原始形态就是**静默**——通道只打一行 INFO 日志，
    「没有推送通道」与「推送成功」在事后完全不可区分。计数把三者分开：
    真投递成功（`delivered`）/ 未配置跳过（`skipped_unconfigured`）/ 投递失败（`failed`）。
    """

    attempted: int = 0
    delivered: int = 0
    skipped_unconfigured: int = 0
    failed: int = 0
    last_error: str | None = None


_PUSH_STATS = PushDeliveryStats()


def push_delivery_stats() -> dict:
    """当前推送投递计数快照（只读；供 /healthz 之外的自检脚本与测试使用）"""
    return {
        "attempted": _PUSH_STATS.attempted,
        "delivered": _PUSH_STATS.delivered,
        "skipped_unconfigured": _PUSH_STATS.skipped_unconfigured,
        "failed": _PUSH_STATS.failed,
        "last_error": _PUSH_STATS.last_error,
    }


def reset_push_delivery_stats() -> None:
    """清零投递计数（测试隔离用；生产无调用方）"""
    _PUSH_STATS.attempted = 0
    _PUSH_STATS.delivered = 0
    _PUSH_STATS.skipped_unconfigured = 0
    _PUSH_STATS.failed = 0
    _PUSH_STATS.last_error = None


class WebhookPushChannel:
    """真实可用的 webhook 推送通道（B-02：把"假的"变成"可配置的真的"）

    ## 为什么是 webhook 而不是厂商 SDK

    uni-push / 华为 / 小米通道都要求 AppID + 证书 + 客户端 SDK 集成，本仓**一个都不在位**
    （客户端 `manifest.json` 无 push 模块）。Webhook 是**零凭证可落地**的那一档：配一个
    URL 就能真的把消息 POST 出去，接企业微信群机器人 / 自建中继 / 后续任何厂商网关都行
    —— 端上真正弹通知仍需要客户端接 push SDK，那一半不在本批范围内，**不谎称已闭环**。

    ## 未配置时的行为（本类最重要的一条）

    **不伪装成功**：`attempted` 计入但 `delivered` 不增，打 WARNING 并在
    `skipped_unconfigured` 累加。绝不"看起来推了"。

    ## 旁路铁律（Lead 明确要求写进 docstring）

    `send()` 是**旁路增强**，绝不参与消息落库事务：内部全量 try/except 吞掉一切异常
    （含 httpx 任意错误、DNS/超时/证书/JSON），并且**必定带超时**（默认
    `settings.push_webhook_timeout_s`）。webhook 挂掉时消息照常落库、用户照常在
    App 内看到——最坏的后果只能是"少了一条通知"，**不能是"消息没存上"**。
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        timeout: float | None = None,
    ) -> None:
        # 延迟读配置：允许测试 monkeypatch settings 后再构造（不在模块导入期固化）
        from app.core.config import settings

        self.url = (url if url is not None else settings.push_webhook_url or "").strip()
        self.token = (token if token is not None else settings.push_webhook_token or "").strip()
        raw_timeout = timeout if timeout is not None else settings.push_webhook_timeout_s
        # 防呆：0/负数会让 httpx 立刻超时或抛错，退化为默认 5s
        self.timeout = raw_timeout if isinstance(raw_timeout, (int, float)) and raw_timeout > 0 else 5.0

    @property
    def configured(self) -> bool:
        """是否已配置 webhook（未配置时 send 只告警不投递）"""
        return bool(self.url)

    def _payload(self, user_id: str, message: Message) -> dict:
        """投递体（幂等键 message.id 供接收端去重；不塞用户隐私之外的东西）"""
        return {
            "channel": "push",
            "user_id": str(user_id),
            "message_id": message.id,
            "msg_type": message.msg_type,
            "title": message.title,
            "body": message.body,
            "payload": message.payload or {},
            "sent_at": message.sent_at.isoformat() if message.sent_at else None,
        }

    def send(self, *, user_id: str, message: Message) -> None:
        """投递一条消息（**绝不抛异常**；未配置/失败都走显式告警 + 计数）"""
        _PUSH_STATS.attempted += 1

        if not self.configured:
            _PUSH_STATS.skipped_unconfigured += 1
            # 只告警一次按等级 WARNING（不是 INFO：INFO 在默认日志级别下就是"静默"，
            # 而本缺陷的原始形态正是"静默地没推"）
            logger.warning(
                "推送未配置（PUSH_WEBHOOK_URL 为空）⇒ 消息 %s 仅落库、**不会**到达手机端。"
                "用户需自行打开 App 消息页才能看到（skip=%d）",
                message.id,
                _PUSH_STATS.skipped_unconfigured,
            )
            return

        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            import httpx

            resp = httpx.post(
                self.url,
                json=self._payload(user_id, message),
                headers=headers,
                timeout=self.timeout,
            )
            if resp.status_code >= 400:
                _PUSH_STATS.failed += 1
                _PUSH_STATS.last_error = f"HTTP {resp.status_code}"
                logger.warning(
                    "推送投递失败 msg=%s HTTP %s（消息已正常落库，仅通知未送达）",
                    message.id,
                    resp.status_code,
                )
                return
            _PUSH_STATS.delivered += 1
            logger.info("推送投递成功 msg=%s HTTP %s", message.id, resp.status_code)
        except Exception as exc:  # noqa: BLE001 —— 旁路铁律：推送失败绝不外泄
            _PUSH_STATS.failed += 1
            _PUSH_STATS.last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "推送投递异常 msg=%s: %s: %s（消息已正常落库，仅通知未送达）",
                message.id,
                type(exc).__name__,
                exc,
            )


def build_push_channel_from_settings() -> PushChannel:
    """按配置构造推送通道（`main.py` lifespan 接线用）

    未配置 `PUSH_WEBHOOK_URL` 时返回 **WebhookPushChannel（未配置态）而不再是 mock**：
    这样"没配置"会以 WARNING + 计数显式暴露，而不是像 mock 那样打一行 INFO 就当作
    "推送已完成"。语义差别即 B-02 的全部价值。
    """
    channel = WebhookPushChannel()
    if channel.configured:
        logger.info("推送通道已启用：WebhookPushChannel → %s", channel.url)
    else:
        # 启动即说清后果（不等第一次推送失败才发现）
        logger.warning(
            "推送通道未配置 webhook（PUSH_WEBHOOK_URL 为空）：消息会照常落库，"
            "但**手机端不会有任何系统通知**。配置 PUSH_WEBHOOK_URL 后重启即可生效。"
        )
    return channel


_push_channel_ref: dict[str, PushChannel | None] = {"channel": None}


def get_push_channel() -> PushChannel:
    """当前推送通道（默认 mock；可 set_push_channel 注入真实厂商实现）"""
    ch = _push_channel_ref["channel"]
    if ch is None:
        ch = MockPushChannel()
        _push_channel_ref["channel"] = ch
    return ch


def set_push_channel(channel: PushChannel | None) -> None:
    """注入/重置推送通道（None 恢复 mock 默认；真实厂商通道配 key 后注入）

    B-02：生产由 `main.py` lifespan 调 `build_push_channel_from_settings()` 后注入
    （此前本函数**全仓零调用** ⇒ 生产恒 mock）。
    """
    _push_channel_ref["channel"] = channel

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


def _coerce_content_id(value) -> str | None:
    """BA1：payload.content_id → messages.content_id（UUID 列）防污染转换

    既有 payload 口径宽松（测试/旧数据可能放非 UUID 占位值，如 "v-1"），
    列是 UUID 类型硬约束——非法值置 None（出参退化为 None，不影响消息本体），
    不让消息建发因关联字段失败。
    """
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        uuid.UUID(value.strip())
    except ValueError:
        # 2026-10-14（台账 §7.23 · 批次 8 续审）：**非法值置 None 要留痕**。原为静默返回 None ⇒
        # 消息的 `content_id` 出参为空、客户端「跳转详情」入口静默失效，而日志里毫无线索
        # （无法区分"消息本就没有关联内容"与"关联值被丢弃"）。口径同 §7.20。
        logger.warning("消息 payload.content_id 非 UUID，已置空：%r", value)
        return None
    return value.strip()


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
        # BA1（迁移 f2a3b4c5d6e7）：消息关联内容——口径对齐既有 payload.content_id
        # （voice_done 见 notify_voice_done / echo 见 echo._create_echo_message 快照），
        # 出参 MessageOut.content_id 由此列下发，客户端跳详情不再解析 payload；
        # 非 UUID 值（旧数据/测试占位）置 None，防 UUID 列污染
        content_id=_coerce_content_id((payload or {}).get("content_id")),
    )
    db.add(msg)
    db.flush([msg])

    if channel == "push":
        # 推送通道：默认 mock；生产由 main.py 用 set_push_channel 注入
        # WebhookPushChannel（B-02）。通道实现内部**绝不抛异常**（旁路铁律），
        # 故此处不需要额外 try/except —— 推送失败不会影响消息落库。
        get_push_channel().send(user_id=user_id, message=msg)
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
