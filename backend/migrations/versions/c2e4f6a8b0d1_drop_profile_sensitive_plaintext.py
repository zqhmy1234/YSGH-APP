"""Retire the B-3 plaintext rollback column after all readers are upgraded."""

import sqlalchemy as sa
from alembic import op
from cryptography.exceptions import InvalidTag

from app.services.profile_sensitive_crypto import (
    decrypt_topic,
    normalize_topic,
    topic_digest,
    validate_profile_field_keys,
)

revision = "c2e4f6a8b0d1"
down_revision = "b6d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    validate_profile_field_keys()
    connection = op.get_bind()
    # Hold the DDL lock during verification so a concurrent write cannot race the check.
    connection.execute(sa.text("LOCK TABLE profile_sensitive IN ACCESS EXCLUSIVE MODE NOWAIT"))
    query = sa.text("SELECT topic, topic_enc, topic_hash FROM profile_sensitive")
    with connection.execute(query) as result:
        for row in result.mappings():
            try:
                decrypted = decrypt_topic(row["topic_enc"])
                digest = topic_digest(decrypted)
            except (InvalidTag, ValueError):
                raise RuntimeError("profile_sensitive contains an invalid encrypted topic") from None
            if digest != row["topic_hash"]:
                raise RuntimeError("profile_sensitive contains a mismatched topic hash")
            if row["topic"] is not None and normalize_topic(row["topic"]) != normalize_topic(decrypted):
                raise RuntimeError("profile_sensitive plaintext does not match the encrypted topic")

    op.drop_constraint("profile_sensitive_user_id_topic_key", "profile_sensitive", type_="unique")
    op.drop_column("profile_sensitive", "topic")


def downgrade() -> None:
    # Phase-one readers only need the nullable column to exist. Encrypted rows stay authoritative.
    op.add_column("profile_sensitive", sa.Column("topic", sa.String(), nullable=True))
    op.create_unique_constraint(
        "profile_sensitive_user_id_topic_key", "profile_sensitive", ["user_id", "topic"]
    )
