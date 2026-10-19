"""B-3 profile topic backfill/preflight. Never prints topic or key material.

Run ``--dry-run`` before the migration to count rows and detect normalized
duplicates. The Alembic migration runs the same batch function atomically.
``--apply`` is available for a partially staged database and commits each
batch; re-running it skips rows already filled.
"""

import argparse

from app.services.profile_sensitive_crypto import decrypt_topic, encrypt_topic, topic_digest
from sqlalchemy import inspect, text


def scan_profile_topics(connection, batch_size: int = 500) -> tuple[int, int]:
    """Return (row count, normalized duplicate count) without exposing values."""
    columns = {col["name"] for col in inspect(connection).get_columns("profile_sensitive")}
    has_cipher = "topic_enc" in columns
    seen: set[tuple[str, str]] = set()
    total = duplicates = last_id = 0
    while True:
        query = (
            "SELECT id, user_id, topic, topic_enc FROM profile_sensitive "
            "WHERE id > :last_id ORDER BY id LIMIT :limit"
            if has_cipher else
            "SELECT id, user_id, topic, NULL AS topic_enc FROM profile_sensitive "
            "WHERE id > :last_id ORDER BY id LIMIT :limit"
        )
        rows = connection.execute(
            text(query),
            {"last_id": last_id, "limit": batch_size},
        ).mappings().all()
        if not rows:
            return total, duplicates
        for row in rows:
            topic = row["topic"] or decrypt_topic(row["topic_enc"])
            marker = (str(row["user_id"]), topic_digest(topic))
            duplicates += marker in seen
            seen.add(marker)
            total += 1
            last_id = row["id"]


def backfill_batch(connection, batch_size: int = 500) -> int:
    """Fill one pending batch in the caller's transaction; safe to resume."""
    rows = connection.execute(
        text("SELECT id, topic, topic_enc FROM profile_sensitive "
             "WHERE topic_enc IS NULL OR topic_hash IS NULL "
             "ORDER BY id LIMIT :limit FOR UPDATE"),
        {"limit": batch_size},
    ).mappings().all()
    for row in rows:
        topic = row["topic"] or decrypt_topic(row["topic_enc"])
        ciphertext, digest = encrypt_topic(topic)
        connection.execute(
            text("UPDATE profile_sensitive SET topic_enc = :ciphertext, "
                 "topic_hash = :digest WHERE id = :id"),
            {"ciphertext": ciphertext, "digest": digest, "id": row["id"]},
        )
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--batch-size", type=int, default=500)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")

    from app.db.session import engine

    with engine.connect() as connection:
        total, duplicates = scan_profile_topics(connection, args.batch_size)
        print(f"profile_sensitive rows={total} normalized_duplicates={duplicates}")
        if duplicates:
            raise SystemExit("Resolve normalized duplicates before migration")
        if args.dry_run:
            return
        columns = {col["name"] for col in inspect(connection).get_columns("profile_sensitive")}
        if not {"topic_enc", "topic_hash"}.issubset(columns):
            raise SystemExit("Run the additive Alembic migration before --apply")
    filled = 0
    while True:
        with engine.begin() as connection:
            count = backfill_batch(connection, args.batch_size)
        filled += count
        if count == 0:
            break
    print(f"backfilled={filled} remaining=0")


if __name__ == "__main__":
    main()
