"""SQLite persistence for the ledger: the only file that imports ``sqlite3``.

The domain store serialises itself to a JSON document; this adapter owns the
transaction that replaces it. A crash between two writes leaves the previous
document intact, which is the whole point of the snapshot/rollback protocol.
"""

from __future__ import annotations

import json
import sqlite3

from ..memory.ledger import MemoryStore


class SqliteMemoryStore(MemoryStore):
    def __init__(self, path: str = ":memory:") -> None:
        super().__init__()
        self._conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        self._conn.execute("CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK (id = 1), doc TEXT NOT NULL)")
        row = self._conn.execute("SELECT doc FROM state WHERE id = 1").fetchone()
        if row is not None:
            self.load_state(json.loads(row[0]))

    def _after_change(self) -> None:
        doc = json.dumps(self.dump_state(), ensure_ascii=False)
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "INSERT INTO state(id, doc) VALUES(1, ?) ON CONFLICT(id) DO UPDATE SET doc = excluded.doc", (doc,)
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def close(self) -> None:
        self._conn.close()
