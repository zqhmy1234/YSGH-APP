"""Checkpoint adapter: agent execution state only, never backend business tables.

The default implementation is SQLite (local/dev). PostgreSQL storage is scoped
to the agent's own schema and is injected as a DSN by the deployment, not by
backend credentials.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any

from ports import PortUnavailable


class CheckpointStore:
    def __init__(self, path: str = "") -> None:
        self._path = path or os.environ.get("AGENT_CHECKPOINT_PATH", ":memory:")
        self._conn: sqlite3.Connection | None = None
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)

    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self._path, isolation_level=None, check_same_thread=False)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS checkpoints ("
                "thread_id TEXT PRIMARY KEY, state TEXT NOT NULL, "
                "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )
        return self._conn

    def load(self, thread_id: str) -> dict[str, Any]:
        if not thread_id:
            raise PortUnavailable("checkpoint", "thread_id required")
        row = self._connection().execute("SELECT state FROM checkpoints WHERE thread_id = ?", (thread_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save(self, thread_id: str, state: dict[str, Any]) -> None:
        if not thread_id:
            raise PortUnavailable("checkpoint", "thread_id required")
        self._connection().execute(
            "INSERT INTO checkpoints(thread_id, state) VALUES(?, ?) "
            "ON CONFLICT(thread_id) DO UPDATE SET state=excluded.state, "
            "updated_at=CURRENT_TIMESTAMP",
            (thread_id, json.dumps(state, ensure_ascii=False)),
        )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()


__all__ = ["CheckpointStore"]
