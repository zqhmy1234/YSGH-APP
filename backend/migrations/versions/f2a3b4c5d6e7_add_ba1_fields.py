"""BA1 字段补齐批：contents 5 列 + messages.content_id + voice taken_at 存量回填

Revision ID: f2a3b4c5d6e7
Revises: f1a2b3c4d5e6
Create Date: 2026-09-07 10:00:00.000000

- contents：duration（音频时长秒）/ remark（用户备注）/ size_bytes（文件体积）/
  tags_json（预留 AI 打标）/ ai_description（预留照片 AI 描述）
- messages：content_id（关联内容，供消息跳详情；回响/voice_done 消息 payload
  已有 content_id 概念，本列对齐口径）
- R9-A2 存量回填：voice 内容 taken_at 为空时回填 created_at
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "f2a3b4c5d6e7"
down_revision: str | Sequence[str] | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "contents",
        sa.Column(
            "duration",
            sa.Integer(),
            nullable=True,
            comment="音频时长秒",
        ),
    )
    op.add_column("contents", sa.Column("remark", sa.Text(), nullable=True))
    op.add_column("contents", sa.Column("size_bytes", sa.BigInteger(), nullable=True))
    op.add_column("contents", sa.Column("tags_json", JSONB(), nullable=True))
    op.add_column("contents", sa.Column("ai_description", sa.Text(), nullable=True))
    op.add_column(
        "messages",
        sa.Column(
            "content_id",
            UUID(as_uuid=False),
            nullable=True,
            comment="关联内容，供消息跳详情",
        ),
    )
    # R9-A2 存量回填：voice 内容 taken_at 为空 → 回填 created_at（语音即录即记，
    # 无拍摄时间真值，创建时间即最佳近似）
    op.execute(
        "UPDATE contents SET taken_at = created_at "
        "WHERE content_type = 'voice' AND taken_at IS NULL"
    )


def downgrade() -> None:
    """Downgrade schema.

    注意：taken_at 回填数据不回滚（回填是对 NULL 的确定性补齐，无原始值可还原，
    且 taken_at 本列为 nullable，保留回填值无害）。
    """
    op.drop_column("messages", "content_id")
    op.drop_column("contents", "ai_description")
    op.drop_column("contents", "tags_json")
    op.drop_column("contents", "size_bytes")
    op.drop_column("contents", "remark")
    op.drop_column("contents", "duration")
