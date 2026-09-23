"""AI 对话域 ORM（chat_messages）—— AG5 接线：承载客户端契约的消息与回复。

为什么要落库（而不是透传不存）：
  ① 客户端契约本身要求 `GET /api/v1/chat/replies/:id`（流式分片轮询/SSE 的取回复入口），
     若回复不落库，该端点无从实现（只能返回 404 或伪造）；
  ② 「问 AI 带上下文」需要会话历史；
  ③ 内测期要能复盘"用户问了什么、智能体答了什么"。

与 `messages` 表（回响/事件消息域）**刻意分开**：那是时间轴消息，语义与生命周期不同，
混用会让"消息已读/未读、事件挂载"等既有逻辑被对话数据污染。
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ChatMessage(Base):
    """一条对话消息（user 或 assistant）。

    `kind` 对齐客户端契约的 reply 形态族（bubble/plain/cards/confirm/typing）；
    用户消息恒为 'user'，仅 assistant 消息参与 `GET /replies/:id`。
    """

    __tablename__ = "chat_messages"
    __table_args__ = (
        Index("ix_chat_messages_user_conv_created", "user_id", "conversation_id", "created_at"),
        Index("ix_chat_messages_user_created", "user_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=sa_text("gen_random_uuid()")
    )
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, comment="归属用户"
    )
    conversation_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), nullable=False, comment="会话 ID（agent 侧作为 thread_id 保证多轮上下文）"
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False, comment="user | assistant")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="消息文本")
    kind: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=sa_text("'bubble'"), comment="回复形态族（契约 reply.kind）"
    )
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True, comment="结构化附加（chips/cards/引用等）")
    agent_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="agent 侧单轮耗时（可观测）")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
