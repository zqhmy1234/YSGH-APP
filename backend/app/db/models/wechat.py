"""微信域 ORM 模型（wechat_messages / user_wechat_bindings，对齐 backend/sql/schema.sql）"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

# wechat_messages.status **取值唯一来源**（D09-10 / B11 声明漂移）
#
# 原状三处不一致：本模块注释只列 3 个值、`services/wechat/service.py` 实际写 5 个值、
# `backend/sql/schema.sql` 注释只列 2 个（且其 DDL 默认值 `'processing'` 与本 ORM 默认 `"processed"`
# **不同**——默认值收敛需改 DDL ⇒ 属漂移迁移，已按台账 §5.10 拍板 10.3③ 登记待专用窗口，
# 本次**只对齐"取值集合"的声明**，不改任何默认值/行为）。
# 服务端实际写入点见 `services/wechat/service.py`（processing/processed/media_failed/sensitive/deleted）。
WECHAT_MESSAGE_STATUSES: tuple[str, ...] = (
    "processing",    # DDL 默认值（schema.sql）；当前代码路径均显式写入，故通常不落此值
    "processed",     # 处理完成（正常）
    "media_failed",  # 企微媒体下载失败
    "sensitive",     # 内容安全命中（不进云端镜像）
    "deleted",       # 用户软删本条（F6：只删本条）
)


class WechatMessage(Base):
    """wechat_messages 表（F6 微信入口：msg_id 幂等，只收不编）"""

    __tablename__ = "wechat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    msg_id: Mapped[str] = mapped_column(String, unique=True)
    user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    msg_type: Mapped[str] = mapped_column(String)  # text / image / link / voice
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="processed")  # 取值见 WECHAT_MESSAGE_STATUSES（上）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# 微信客服会话渠道取值（`user_wechat_bindings.channel` 唯一来源）
WECHAT_BINDING_CHANNELS: tuple[str, ...] = (
    "wechat_kf",    # 企微「微信客服」（F6 主链，回调 FromUserName 即本渠道 openid）
    "wechat_app",   # 微信开放平台 App/小程序登录（unionid）
    "miniprogram",  # 小程序
)


class UserWechatBinding(Base):
    """user_wechat_bindings 表（**D09-1 修复的核心载体** · 2026-09-25 功能修复波）。

    ## 为什么现在才有这个 ORM 模型

    该表自 schema.sql 起就存在（`-- 一个 unionid 多 openid`），但**从未映射到 ORM**，
    于是：
      · baseline 迁移 `431bcaa8bd54` 把它当"ORM 未映射的遗留空表" **DROP 掉**；
      · `api/wechat.py` 的回调**不传 `user_id`** ⇒ F6 主链（微信收 → 下载媒体 →
      建 Content → 入管线 → 产记忆）**整条不通**：回调只写 `wechat_messages`。
    修复＝补 ORM 映射 + 幂等重建迁移 + 回调侧按 `FromUserName` 解析绑定 + 绑定端点。

    ## 一个用户多 openid

    同一用户在不同渠道（客服/小程序/App）的 openid 各不相同（unionid 相同），故
    `openid` 唯一、`user_id` 非唯一——与 schema.sql 注释一致。
    """

    __tablename__ = "user_wechat_bindings"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False)
    openid: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    channel: Mapped[str] = mapped_column(Text, nullable=False)  # 取值见 WECHAT_BINDING_CHANNELS
    bound_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
