"""Wave 0 并行开发数据层前瞻（2026-08-26）

背景：CI 用 backend/sql/schema.sql 初始化测试库，但迁移历史与 schema.sql 漂移——
  - upload_tasks / upload_chunks（S5-03 分片上传）只有 ORM 定义、无迁移 → 生产 alembic upgrade 缺表
  - profile_sensitive / sensitive_words 在 baseline 中被 DROP（遗留空表清单），但 B5b/B1 需要重建
  - B1 低置信度事件池（设计 2.3"<0.7 进池周级复核"）无表
本迁移一次性补齐，后续波次 Agent 只写服务层、不再改 models/迁移。

Revision ID: b0b1c2d3e4f5
Revises: 4d00dfec7b46
Create Date: 2026-08-26
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b0b1c2d3e4f5"
down_revision: str | Sequence[str] | None = "4d00dfec7b46"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- upload_tasks / upload_chunks（对齐 models.UploadTask / UploadChunk）----
    # 本地开发库可能已手动建过（非 alembic 管理），用 IF NOT EXISTS 兼容，不 DROP 有数据表
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS upload_tasks (
            id uuid PRIMARY KEY,
            user_id uuid NOT NULL,
            client_upload_id varchar NOT NULL,
            file_name varchar NOT NULL,
            file_size bigint NOT NULL,
            chunk_size bigint NOT NULL,
            chunk_count bigint NOT NULL,
            file_key varchar NOT NULL,
            storage varchar DEFAULT 'fake' NOT NULL,
            status varchar DEFAULT 'pending' NOT NULL,
            completed_at timestamptz,
            created_at timestamptz DEFAULT now() NOT NULL,
            updated_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT uq_upload_tasks_user_client UNIQUE (user_id, client_upload_id)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_upload_tasks_user_id ON upload_tasks (user_id)")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS upload_chunks (
            id uuid PRIMARY KEY,
            upload_id uuid NOT NULL,
            chunk_index bigint NOT NULL,
            chunk_hash varchar NOT NULL,
            size bigint NOT NULL,
            status varchar DEFAULT 'uploaded' NOT NULL,
            created_at timestamptz DEFAULT now() NOT NULL,
            CONSTRAINT uq_upload_chunks_task_index UNIQUE (upload_id, chunk_index)
        )
        """
    )
    op.execute("CREATE INDEX IF NOT EXISTS ix_upload_chunks_upload_id ON upload_chunks (upload_id)")

    # ---- profile_sensitive 重建（5 级处置，对齐 B1 v1.1 修订 + B5b §4）----
    # 旧结构在 baseline 被 DROP 且无 ORM 无数据，直接 DROP IF EXISTS 后重建
    op.execute("DROP TABLE IF EXISTS profile_sensitive")
    op.create_table(
        "profile_sensitive",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("topic", sa.String(), nullable=False),
        sa.Column("topic_hash", sa.String(), nullable=True),
        sa.Column("disposition", sa.String(), server_default="forbid", nullable=False),
        # allow / mention / caution / review / forbid（话题×处置 5 级）
        sa.Column("evidence", sa.JSON(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("locked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("profile_sensitive_user_id_fkey")),
        sa.UniqueConstraint("user_id", "topic", name=op.f("profile_sensitive_user_id_topic_key")),
    )

    # ---- sensitive_words 重建（三层词表，对齐 schema.sql 快照）----
    op.execute("DROP TABLE IF EXISTS sensitive_words")
    op.create_table(
        "sensitive_words",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("word", sa.String(), nullable=False),
        sa.Column("level", sa.Integer(), server_default="1", nullable=False),
        # 1=预置基础词表 2=画像敏感标记驱动 3=违规词回流
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("sensitive_words_user_id_fkey")),
        sa.UniqueConstraint("word", "user_id", name=op.f("sensitive_words_word_user_id_key")),
    )

    # ---- profile_annotation_pool（B1 低置信度事件池，设计 2.3）----
    op.create_table(
        "profile_annotation_pool",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event_id", sa.String(), nullable=True),
        sa.Column("raw_text", sa.String(), nullable=False),
        sa.Column("dimension", sa.String(), nullable=True),
        sa.Column("candidate_value", sa.String(), nullable=True),
        sa.Column("confidence", sa.Double(), server_default="0", nullable=False),
        sa.Column("status", sa.String(), server_default="pending", nullable=False),
        # pending / reviewed / confirmed / discarded
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("profile_annotation_pool_user_id_fkey")),
    )
    op.create_index("ix_profile_annotation_pool_user", "profile_annotation_pool", ["user_id", "status"])


def downgrade() -> None:
    op.drop_table("profile_annotation_pool")
    op.drop_table("sensitive_words")
    op.drop_table("profile_sensitive")
    op.drop_index("ix_upload_chunks_upload_id", table_name="upload_chunks")
    op.drop_table("upload_chunks")
    op.drop_index("ix_upload_tasks_user_id", table_name="upload_tasks")
    op.drop_table("upload_tasks")
