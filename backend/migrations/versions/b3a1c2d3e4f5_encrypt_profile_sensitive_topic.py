"""B-3 encrypt profile-sensitive topics and add a per-user blind index.

Existing plaintext is retained for one rollout as rollback data. The next
explicit migration may remove it after production verification; new writes
only populate topic_enc/topic_hash. No key material belongs in this file.
"""

from alembic import op
import sqlalchemy as sa

from app.services.profile_sensitive_crypto import decrypt_topic, validate_profile_field_keys
from scripts.backfill_profile_sensitive import backfill_batch, scan_profile_topics

revision = "b3a1c2d3e4f5"
down_revision = "6a12e10c4868"
branch_labels = None
depends_on = None


def upgrade() -> None:
    validate_profile_field_keys()
    connection = op.get_bind()
    _, duplicates = scan_profile_topics(connection)
    if duplicates:
        raise RuntimeError("profile_sensitive has normalized duplicates; resolve before B-3 migration")
    op.add_column("profile_sensitive", sa.Column("topic_enc", sa.Text(), nullable=True))
    while backfill_batch(connection):
        pass
    remaining = connection.execute(sa.text(
        "SELECT count(*) FROM profile_sensitive WHERE topic_enc IS NULL OR topic_hash IS NULL"
    )).scalar_one()
    if remaining:
        raise RuntimeError("profile_sensitive encryption backfill incomplete")
    op.alter_column("profile_sensitive", "topic_enc", nullable=False)
    op.alter_column("profile_sensitive", "topic_hash", existing_type=sa.String(), nullable=False)
    op.alter_column("profile_sensitive", "topic", existing_type=sa.String(), nullable=True)
    op.create_unique_constraint(
        "profile_sensitive_user_id_topic_hash_key", "profile_sensitive", ["user_id", "topic_hash"]
    )


def downgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text(
        "SELECT id, topic_enc FROM profile_sensitive WHERE topic IS NULL"
    )).mappings()
    for row in rows:
        connection.execute(
            sa.text("UPDATE profile_sensitive SET topic = :topic WHERE id = :id"),
            {"topic": decrypt_topic(row["topic_enc"]), "id": row["id"]},
        )
    op.drop_constraint("profile_sensitive_user_id_topic_hash_key", "profile_sensitive", type_="unique")
    op.alter_column("profile_sensitive", "topic", existing_type=sa.String(), nullable=False)
    op.alter_column("profile_sensitive", "topic_hash", existing_type=sa.String(), nullable=True)
    op.drop_column("profile_sensitive", "topic_enc")
