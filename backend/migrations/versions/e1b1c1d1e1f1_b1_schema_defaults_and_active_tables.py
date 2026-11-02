"""B-1: preserve active raw-SQL tables and align database defaults.

Existing databases keep all data and legacy tables. Fresh databases use the
ORM bootstrap in env.py; this revision is the incremental path for old ones.

Revision ID: e1b1c1d1e1f1
Revises: d9a0b1c2d3e4
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1b1c1d1e1f1"
down_revision: str | Sequence[str] | None = "d9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Defaults present in schema.sql but absent from the ORM before B-1. Keeping
# this migration explicit makes its historical effect independent of models.
_DEFAULTS = {
    "users": {"id": "gen_random_uuid()", "status": "1"},
    "devices": {"id": "gen_random_uuid()"},
    "contents": {
        "id": "gen_random_uuid()",
        "sensitive_status": "'正常'",
        "status": "'processing'",
    },
    "events": {"id": "gen_random_uuid()", "status": "'draft'"},
    "user_profile": {"version": "1", "dimensions": "'{}'", "token_usage": "0"},
    "profile_dimension_pending": {"count": "1", "status": "'pending'"},
    "profile_sensitive": {
        "disposition": "'forbid'",
        "evidence": "'[]'",
        "locked": "false",
    },
    "profile_annotation_pool": {"confidence": "0", "status": "'pending'"},
    "correction_log": {"content_type": "'text'", "is_global_candidate": "false"},
    "sync_state": {"cursor_version": "0"},
    "offline_queue": {"status": "'pending'", "retry_count": "0"},
    "deleted_logs": {"cleanup_status": "'pending'"},
    "sync_field_versions": {"deleted": "false"},
    "wechat_messages": {"status": "'processing'"},
    "upload_tasks": {
        "id": "gen_random_uuid()",
        "storage": "'fake'",
        "status": "'pending'",
    },
    "upload_chunks": {"id": "gen_random_uuid()", "status": "'uploaded'"},
    "messages": {"channel": "'in_app'", "payload": "'{}'", "status": "'unread'"},
}


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS profile_l2_evidence (
            id bigserial PRIMARY KEY,
            dimension text NOT NULL,
            user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            evidence_content_ids jsonb NOT NULL DEFAULT '[]',
            created_at timestamptz NOT NULL DEFAULT now()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_l2_evidence_user_dim
        ON profile_l2_evidence (user_id, dimension)
    """)
    op.execute("""
        CREATE TABLE IF NOT EXISTS finetune_jobs (
            id bigserial PRIMARY KEY,
            trigger text NOT NULL,
            dataset_count int NOT NULL DEFAULT 0,
            model text,
            status text NOT NULL DEFAULT 'pending',
            started_at timestamptz,
            finished_at timestamptz
        )
    """)
    for table, columns in _DEFAULTS.items():
        for column, default in columns.items():
            op.alter_column(table, column, server_default=sa.text(default))


def downgrade() -> None:
    # Reverting defaults or removing tables could break existing raw-SQL writers
    # and discard evidence/training records. No destructive automatic rollback.
    pass
