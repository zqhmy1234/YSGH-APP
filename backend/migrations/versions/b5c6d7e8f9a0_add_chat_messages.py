"""AI 对话域：chat_messages 表（AG5 客户端契约接线）

Revision ID: b5c6d7e8f9a0
Revises: a4b5c6d7e8f9
Create Date: 2026-09-23 22:30:00.000000

背景：客户端 AI 页（`client/components/TabAi/TabAi.uvue` 头注释）的契约是
  POST /api/v1/chat/messages  { conversation_id?, content, attachments?[] } → { reply }
  GET  /api/v1/chat/replies/:id （流式分片轮询/SSE，形态由 reply.kind 决定）
其中 GET 取回复要求**回复可持久化**，故建此表（不落库则该端点无从实现，只能 404 或伪造）。

与 `messages` 表刻意分开：那是回响/事件消息域，生命周期与语义不同，混用会污染既有逻辑。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "b5c6d7e8f9a0"
down_revision: str | Sequence[str] | None = "a4b5c6d7e8f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "chat_messages",
        sa.Column(
            "id",
            UUID(as_uuid=False),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", UUID(as_uuid=False), nullable=False, comment="归属用户"),
        sa.Column("conversation_id", UUID(as_uuid=False), nullable=False, comment="会话 ID（agent 侧 thread_id）"),
        sa.Column("role", sa.String(length=16), nullable=False, comment="user | assistant"),
        sa.Column("content", sa.Text(), nullable=False, comment="消息文本"),
        sa.Column(
            "kind",
            sa.String(length=32),
            server_default=sa.text("'bubble'"),
            nullable=False,
            comment="回复形态族（契约 reply.kind）",
        ),
        sa.Column("payload", JSONB(astext_type=sa.Text()), nullable=True, comment="结构化附加（chips/cards/引用等）"),
        sa.Column("agent_latency_ms", sa.Integer(), nullable=True, comment="agent 侧单轮耗时（可观测）"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="chat_messages_user_id_fkey", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="chat_messages_pkey"),
    )
    op.create_index(
        "ix_chat_messages_user_conv_created",
        "chat_messages",
        ["user_id", "conversation_id", "created_at"],
        unique=False,
    )
    op.create_index("ix_chat_messages_user_created", "chat_messages", ["user_id", "created_at"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_chat_messages_user_created", table_name="chat_messages")
    op.drop_index("ix_chat_messages_user_conv_created", table_name="chat_messages")
    op.drop_table("chat_messages")
