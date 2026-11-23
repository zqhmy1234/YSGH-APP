"""PostgreSQL persistence for the memory ledger.

The domain store is storage-agnostic; this adapter owns one transaction that
replaces the whole JSON document, so a crash leaves the previous snapshot
intact. Mirrors ``SqliteMemoryStore`` semantics exactly (same dump/load hooks).

Only this file is allowed to import ``psycopg``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from memory.ledger import MemoryStore
from ports import PortUnavailable


class PgMemoryStore(MemoryStore):
    def __init__(self, dsn: str = "", *, connection: Any | None = None) -> None:
        super().__init__()
        self._owns = connection is None
        if connection is None:
            try:
                import psycopg  # type: ignore
            except Exception as exc:  # pragma: no cover - exercised only without driver
                raise PortUnavailable("pg", f"psycopg not installed: {exc}") from exc
            if not dsn:
                raise PortUnavailable("pg", "PG_DSN not configured")
            connection = psycopg.connect(dsn)
        self._conn = connection
        self._ensure_schema()
        row = self._fetch()
        if row is not None:
            self.load_state(json.loads(row))

    def _ensure_schema(self) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS agent_v3_memory_state ("
                "id SMALLINT PRIMARY KEY CHECK (id = 1), doc JSONB NOT NULL)"
            )
        self._conn.commit()

    def _fetch(self) -> str | None:
        with self._conn.cursor() as cur:
            cur.execute("SELECT doc FROM agent_v3_memory_state WHERE id = 1")
            row = cur.fetchone()
        return None if row is None else row[0]

    def _after_change(self) -> None:
        state = self.dump_state()
        doc = json.dumps(state, ensure_ascii=False)
        with self._conn.cursor() as cur:
            cur.execute(
                "INSERT INTO agent_v3_memory_state(id, doc) VALUES(1, %s::jsonb) "
                "ON CONFLICT (id) DO UPDATE SET doc = EXCLUDED.doc",
                (doc,),
            )
            # Normalized projections are derived from the same state; re-upserting
            # them in the same transaction keeps them rebuildable and queryable.
            for fact in state.get("facts", {}).values():
                tags = fact.get("tags", {}) or {}
                cur.execute(
                    "INSERT INTO agent_v3_memory_tags(entry_id, person_id, library, category, content_type, mood, topics, content_format, image_route) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s) "
                    "ON CONFLICT (entry_id) DO UPDATE SET library=EXCLUDED.library, category=EXCLUDED.category, "
                    "content_type=EXCLUDED.content_type, mood=EXCLUDED.mood, topics=EXCLUDED.topics, "
                    "content_format=EXCLUDED.content_format, image_route=EXCLUDED.image_route, updated_at=now()",
                    (
                        fact["entry_id"],
                        fact["person_id"],
                        tags.get("library", ""),
                        tags.get("category", ""),
                        tags.get("content_type", ""),
                        tags.get("mood", ""),
                        json.dumps(tags.get("topics", []), ensure_ascii=False),
                        tags.get("content_format", "text"),
                        tags.get("image_route", ""),
                    ),
                )
            for link in state.get("links", []):
                cur.execute(
                    "INSERT INTO agent_v3_links(person_id, memory_id, collection_id, note, created_at) "
                    "VALUES(%s,%s,%s,%s,%s::timestamptz) "
                    "ON CONFLICT (person_id, memory_id, collection_id) DO NOTHING",
                    (
                        link["person_id"],
                        link["memory_id"],
                        link["collection_id"],
                        link.get("note", ""),
                        link.get("created_at") or datetime.now(UTC).isoformat(),
                    ),
                )
        self._conn.commit()

    def close(self) -> None:
        if self._owns:
            self._conn.close()
