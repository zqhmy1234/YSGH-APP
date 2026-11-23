"""PG adapter tests use a fake connection: same dump/load hooks as SQLite."""

from __future__ import annotations

from agentv3.adapters.pg_memory import PgMemoryStore
from agentv3.contracts import MemoryLayer, OpKind, MemoryProposal, ProposalBatch


class _Cur:
    def __init__(self, db):
        self.db = db
        self._row = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if sql.startswith("CREATE"):
            self.db.created = True
        elif sql.startswith("SELECT"):
            self._row = (self.db.doc,) if self.db.doc is not None else None
        elif sql.startswith("INSERT") and "agent_v3_memory_state" in sql:
            self.db.doc = params[0]
        elif sql.startswith("INSERT"):
            self.db.projection_writes += 1

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self):
        self.doc = None
        self.created = False
        self.commits = 0
        self.projection_writes = 0

    def cursor(self):
        return _Cur(self)

    def commit(self):
        self.commits += 1


def _proposal(text="她喜欢桂花"):
    return MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.FACT, text=text, evidence=("e1",), entry_id=None)


def test_pg_store_persists_and_reloads_with_same_hooks():
    conn = _FakeConn()
    store = PgMemoryStore(connection=conn)
    receipt = store.commit(ProposalBatch(person_id="p1", batch_hash="hash-0001", proposals=(_proposal(),)))
    assert receipt.idempotent is False
    assert "她喜欢桂花" in conn.doc
    assert conn.commits >= 1


def test_pg_store_loads_previous_document_and_is_idempotent():
    conn = _FakeConn()
    first = PgMemoryStore(connection=conn)
    first.commit(ProposalBatch(person_id="p1", batch_hash="hash-0001", proposals=(_proposal(),)))
    second = PgMemoryStore(connection=conn)
    assert second.entries("p1")[0].text == "她喜欢桂花"
    again = second.commit(ProposalBatch(person_id="p1", batch_hash="hash-0001", proposals=(_proposal(),)))
    assert again.idempotent is True


def test_pg_store_without_dsn_is_unavailable_not_silent(monkeypatch):
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *a, **k):
        if name == "psycopg":
            raise ModuleNotFoundError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    try:
        PgMemoryStore(dsn="")
    except Exception as exc:
        assert type(exc).__name__ == "PortUnavailable"
    else:  # pragma: no cover
        raise AssertionError("expected PortUnavailable")
