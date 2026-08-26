"""ORM 模型（对齐 backend/sql/schema.sql）

当前覆盖：认证域（users/devices/sms_codes）+ 内容域（contents）——
支撑 S1-02 认证真实接入 + S1-02/API-002 内容入库主链路。
"""
import uuid
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Float, Index, String, Text, UniqueConstraint, func
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    """users 表（决策 #8：unionid 主键 + 手机号备用）"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    unionid: Mapped[str | None] = mapped_column(String, unique=True)
    phone: Mapped[str | None] = mapped_column(String, unique=True)
    nickname: Mapped[str | None] = mapped_column(String)
    avatar: Mapped[str | None] = mapped_column(String)
    status: Mapped[int] = mapped_column(BigInteger, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)


class Device(Base):
    """devices 表（refresh_token 可吊销，AUTH-006）

    修复（审查 MAJOR 遗漏）：补 UNIQUE(user_id, device_id)（schema.sql 已有，ORM 漂移）
    """

    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("user_id", "device_id", name="uq_devices_user_device"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    device_id: Mapped[str] = mapped_column(String)
    platform: Mapped[str] = mapped_column(String)
    # TD-P3 M6（审查中危/低危）：refresh_token 不再明文落库——只存哈希 + 最后轮换时间。
    # refresh_token 明文列保留用于迁移期兼容（存量行哈希化为空时回退比对；登录即覆写清空）。
    refresh_token: Mapped[str | None] = mapped_column(String, nullable=True)
    refresh_token_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    refresh_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SmsCode(Base):
    """sms_codes 表（防刷：限流+有效期，AUTH-003/004）"""

    __tablename__ = "sms_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String, index=True)
    code: Mapped[str] = mapped_column(String)
    expire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Content(Base):
    """contents 核心表（照片/文字/语音/文章统一入库，API-002）

    去重（Q16）：同用户 perceptual_hash 唯一（PG 多 NULL 不冲突，
    text/voice 无哈希不受约束）；修复：ORM 补唯一约束防并发双写（审查 MAJOR）。

    R4#4（创建端点幂等键）：client_generated_id 为客户端生成的幂等键，
    (user_id, client_generated_id) 部分唯一索引（PG 多 NULL 不冲突 → 仅非空参与），
    photo/voice 既有幂等（perceptual_hash 409 / cos_key）保留为兜底。
    """

    __tablename__ = "contents"
    __table_args__ = (
        UniqueConstraint("user_id", "perceptual_hash", name="uq_contents_user_hash"),
        Index(
            "uq_contents_user_client_generated_id",
            "user_id",
            "client_generated_id",
            unique=True,
            postgresql_where=sa_text("client_generated_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    content_type: Mapped[str] = mapped_column(String)
    content_class: Mapped[str | None] = mapped_column(String, nullable=True)
    class_source: Mapped[str | None] = mapped_column(String, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gps_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    place: Mapped[str | None] = mapped_column(String, nullable=True)
    perceptual_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    client_generated_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=False
    )  # R4#4 幂等键（客户端生成，同用户唯一）
    emotion: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sensitive_tags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sensitive_status: Mapped[str] = mapped_column(String, default="正常")
    qdrant_text_id: Mapped[str | None] = mapped_column(String, nullable=True)
    qdrant_image_id: Mapped[str | None] = mapped_column(String, nullable=True)
    cos_key: Mapped[str | None] = mapped_column(String, nullable=True)
    thumbnail_key: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, default="app")
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String, default="processing")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)


class CorrectionLog(Base):
    """correction_log 表（B5-c 三层裁决第①层：个人规则数据源）

    对齐 schema.sql 纠错域：
    - content_embedding 按 schema 注记改为 qdrant_point_id（MVP 向量检索走 Qdrant，零新增）
    - 同内容多次纠错以最后一次为准（查询时按 content_id 取最新）
    - 保留最近 500 条/用户（超出由 service 层裁剪）
    - is_global_candidate：共性纠错标记（多用户一致 → 全局微调候选，≥50 触发）
    """

    __tablename__ = "correction_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    content_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    content_type: Mapped[str] = mapped_column(String, default="text")
    qdrant_point_id: Mapped[str | None] = mapped_column(String, nullable=True)
    old_label: Mapped[str | None] = mapped_column(String, nullable=True)
    new_label: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String, default="active")  # active / echo / org
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_global_candidate: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SyncState(Base):
    """sync_state 表（B4-2：每端同步游标，增量拉取幂等）"""

    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    device_id: Mapped[str] = mapped_column(String)
    cursor_version: Mapped[int] = mapped_column(BigInteger, default=0)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OfflineQueue(Base):
    """offline_queue 表（B4-3：云端幂等去重 + 变更日志，op_id 按用户唯一）

    双角色：
    1. 客户端操作幂等（(user_id, op_id) 唯一：网络重试同一操作只执行一次）
    2. 增量拉取源（id 全局单调 = 同步游标）

    安全修复：op_id 唯一约束从全局改为 (user_id, op_id) 复合——
    防跨用户 op_id 碰撞导致他人操作被幂等跳过（审查 CRITICAL）。
    """

    __tablename__ = "offline_queue"
    __table_args__ = (UniqueConstraint("user_id", "op_id", name="uq_offline_queue_user_op"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    op_id: Mapped[str] = mapped_column(String)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    device_id: Mapped[str] = mapped_column(String)
    op_type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String, default="done")
    retry_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DeletedLog(Base):
    """deleted_logs 表（B4-2：软删除 30 天物理清理对账）"""

    __tablename__ = "deleted_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    content_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    deleted_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    cleanup_status: Mapped[str] = mapped_column(String, default="pending")


class SyncFieldVersion(Base):
    """sync_field_versions 表（B4-2：字段级 LWW 版本存储，云端权威）

    每 (entity_type, entity_id, field) 一行：value + updated_at + user_id（归属）；
    LWW 比较：客户端 updated_at > 云端 → 更新；否则云端胜。
    deleted 标记 entity 级软删除墓碑（同步到各端）。
    user_id：实体归属（越权校验：push 时非本人实体拒绝，B4 安全修复）。
    """

    __tablename__ = "sync_field_versions"

    entity_type: Mapped[str] = mapped_column(String, primary_key=True)
    entity_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    field: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted: Mapped[bool] = mapped_column(default=False)


class Event(Base):
    """events 表（B3 四层事件模型 L0-L3；2026-08-20 管线接线新增 ORM）"""

    __tablename__ = "events"
    __table_args__ = (
        # S-SY-1（B3-6 端侧 L0/L1 真值）：客户端事件幂等键（同用户唯一）。
        # PG 多 NULL 不冲突 → 部分唯一索引：仅非空 client_event_id 参与唯一约束。
        Index(
            "uq_events_user_client_event",
            "user_id",
            "client_event_id",
            unique=True,
            postgresql_where=sa_text("client_event_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    client_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=False)
    level: Mapped[int] = mapped_column(default=0)  # 0-3
    parent_event_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    title_source: Mapped[str | None] = mapped_column(String, nullable=True)
    cover_content_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    place: Mapped[str | None] = mapped_column(String, nullable=True)
    emotion: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sensitivity: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default="draft")
    generated_by: Mapped[str] = mapped_column(String, default="cloud")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)


class EventItem(Base):
    """event_items 表（content_id, event_id 多对多；层级 JOIN events.level）"""

    __tablename__ = "event_items"

    content_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    event_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventEditLog(Base):
    """event_edit_log 表（B3-5 用户合并/拆分/确认/重命名痕迹；AGG-013 用户操作优先）"""

    __tablename__ = "event_edit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    action: Mapped[str] = mapped_column(String)  # merge/split/confirm/rename
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EchoHistory(Base):
    """echo_history 表（B5-a：回响每天 ≤1 条 / 划掉不再出现）

    修复（审查 MAJOR 竞态）：UNIQUE(user_id, event_id) 因 event_id 恒为 NULL 失效
    （PG 多 NULL 不冲突）。补部分唯一索引 (user_id, shown_date) WHERE action
    <> 'dismiss'——DB 层兜底"每天 ≤1 条"，并发双请求只有一条能插入。
    shown_date：展示日（本地日界，应用写入；timestamptz::date 非 IMMUTABLE 不能建索引，
    故显式落列，日界口径单一来源 = 应用侧 _local_now()）。
    """

    __tablename__ = "echo_history"
    __table_args__ = (
        Index(
            "uq_echo_history_daily",
            "user_id",
            "shown_date",
            unique=True,
            postgresql_where=sa_text("action IS DISTINCT FROM 'dismiss'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    event_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    shown_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    shown_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    action: Mapped[str | None] = mapped_column(String, nullable=True)  # respond / dismiss / suppressed
    fingerprint: Mapped[str | None] = mapped_column(String, nullable=True)


class WechatMessage(Base):
    """wechat_messages 表（F6 微信入口：msg_id 幂等，只收不编）"""

    __tablename__ = "wechat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    msg_id: Mapped[str] = mapped_column(String, unique=True)
    user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    msg_type: Mapped[str] = mapped_column(String)  # text / image / link / voice
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="processed")  # processed / failed / deleted
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class UserProfile(Base):
    """user_profile 表（B1：稀疏高维枚举画像，冷启动三问激活 L0/L1）"""

    __tablename__ = "user_profile"

    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    version: Mapped[int] = mapped_column(default=1)
    dimensions: Mapped[dict] = mapped_column(JSONB, default=dict)
    token_usage: Mapped[int] = mapped_column(BigInteger, default=0)
    last_rebuilt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProfileDimensionHistory(Base):
    """profile_dimension_history 表（B1：历史值保留最近 10 条）"""

    __tablename__ = "profile_dimension_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    dimension: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProfileDimensionPending(Base):
    """profile_dimension_pending 表（B1 维度扩展队列）

    枚举集无合适值 → 不自动加（标注是映射不是生成），原始回答进本队列，
    累计同类后人工确认再扩枚举（B1 2.3：累计 N 次同类 → 人工确认后加值）。
    """

    __tablename__ = "profile_dimension_pending"
    __table_args__ = (
        UniqueConstraint("user_id", "dimension", "raw_answer", name="uq_pdp_user_dim_raw"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    dimension: Mapped[str] = mapped_column(String)  # relation_core / life_events / values_priority
    raw_answer: Mapped[str] = mapped_column(String)  # 未命中枚举的原始回答
    count: Mapped[int] = mapped_column(BigInteger, default=1)  # 同类累计
    status: Mapped[str] = mapped_column(String, default="pending")  # pending / confirmed / rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProfileSensitive(Base):
    """profile_sensitive 表（B1 v1.1 修订 + B5b §4：画像级敏感，永不过期）

    话题×处置 5 级（allow/mention/caution/review/forbid）+ 证据 + 生命周期；
    红线级（涉政/违法/未成年/医疗诊断）不进画像，走 B5-b 护栏硬规则。
    Wave 0 重建（迁移 b0b1c2d3e4f5），供 B5b 回响双查 L1 校验接线。
    """

    __tablename__ = "profile_sensitive"
    __table_args__ = (
        UniqueConstraint("user_id", "topic", name="profile_sensitive_user_id_topic_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    topic: Mapped[str] = mapped_column(String)
    topic_hash: Mapped[str | None] = mapped_column(String, nullable=True)  # HMAC 盲索引预留
    disposition: Mapped[str] = mapped_column(String, default="forbid")  # allow/mention/caution/review/forbid
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    locked: Mapped[bool] = mapped_column(default=False)  # 用户显式标记
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SensitiveWord(Base):
    """sensitive_words 表（B5b 三层敏感词表：预置基础/画像标记驱动/违规词回流）

    Wave 0 重建（迁移 b0b1c2d3e4f5），供事件级敏感分类器与违规词回流使用。
    """

    __tablename__ = "sensitive_words"
    __table_args__ = (
        UniqueConstraint("word", "user_id", name="sensitive_words_word_user_id_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    word: Mapped[str] = mapped_column(String)
    level: Mapped[int] = mapped_column(default=1)  # 1=预置 2=画像标记驱动 3=违规词回流
    user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)  # NULL=全局
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProfileAnnotationPool(Base):
    """profile_annotation_pool 表（B1 低置信度事件池，设计 2.3）

    置信度 <0.7 的标注候选不进画像，入本池周级批量复核；
    Wave 0 新增（迁移 b0b1c2d3e4f5）。
    """

    __tablename__ = "profile_annotation_pool"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_text: Mapped[str] = mapped_column(String)
    dimension: Mapped[str | None] = mapped_column(String, nullable=True)
    candidate_value: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float] = mapped_column(default=0.0)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending/reviewed/confirmed/discarded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Message(Base):
    """messages 表（S4-07 推送 + S4-08 消息中心）

    统一消息中心：in-app 消息（关怀追问/回响）与 push 记录（每日复盘/语音完成）
    同表存储；产品部推送策略：复盘 push / 回响 in-app / 关怀追问 in-app。
    推送厂商凭证未配置 → mock 通道（notify.py 日志占位），配 key 后零切换。
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    channel: Mapped[str] = mapped_column(String, default="in_app")  # in_app / push
    msg_type: Mapped[str] = mapped_column(String)  # daily_review / voice_done / care_followup / echo
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)  # 附加数据（内容 id / 语音 id / 模板标记）
    status: Mapped[str] = mapped_column(String, default="unread")  # unread / read / archived
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

class UploadTask(Base):
    """upload_tasks 表（S5-03 COS 分片上传/断电续传，WP-C 2026-08-19）

    client_upload_id 为客户端幂等键（同一文件重传复用任务），
    file_key 为最终对象键；分片状态在 upload_chunks。
    """

    __tablename__ = "upload_tasks"
    __table_args__ = (
        UniqueConstraint("user_id", "client_upload_id", name="uq_upload_tasks_user_client"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    client_upload_id: Mapped[str] = mapped_column(String)
    file_name: Mapped[str] = mapped_column(String)
    file_size: Mapped[int] = mapped_column(BigInteger)
    chunk_size: Mapped[int] = mapped_column(BigInteger)
    chunk_count: Mapped[int] = mapped_column(BigInteger)
    file_key: Mapped[str] = mapped_column(String)
    storage: Mapped[str] = mapped_column(String, default="fake")
    status: Mapped[str] = mapped_column(String, default="pending")  # pending/uploading/completed/failed
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UploadChunk(Base):
    """upload_chunks 表（分片状态：断电续传依据）"""

    __tablename__ = "upload_chunks"
    __table_args__ = (
        UniqueConstraint("upload_id", "chunk_index", name="uq_upload_chunks_task_index"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    upload_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    chunk_index: Mapped[int] = mapped_column(BigInteger)
    chunk_hash: Mapped[str] = mapped_column(String)
    size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String, default="uploaded")  # uploaded 即已落存储
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GeoCache(Base):
    """geo_cache 表（高德逆地理缓存 · 外部API清单 #5）

    geohash 精度 6（≈1.2km 格子）作缓存键，同格复用一次逆编码调用；
    高德合规：逆地理结果不可缓存超 30 天（service 层读取时校验 updated_at 年龄）。
    """

    __tablename__ = "geo_cache"

    geohash: Mapped[str] = mapped_column(String, primary_key=True)  # 精度 6
    place: Mapped[str | None] = mapped_column(String, nullable=True)
    city: Mapped[str | None] = mapped_column(String, nullable=True)
    province: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


