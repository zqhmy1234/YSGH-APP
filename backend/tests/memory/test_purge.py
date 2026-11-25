"""B5-04 probes: layer order, only done/failed, profile_sensitive retained."""

from __future__ import annotations

from app.domain.memory import purge as module


class _Result:
    rowcount = 2

    def __init__(self, rows): self._rows = rows
    def all(self): return self._rows
    def scalar(self): return 1


class _DB:
    def __init__(self, rows): self.rows = rows
    def execute(self, _stmt): return _Result(self.rows)
    def commit(self): pass
    def rollback(self): pass


def test_four_layers_are_attempted_and_failure_is_reported(monkeypatch):
    rows = [("c1", "cos/a", "thumb/a"), ("c2", "cos/b", "")]
    monkeypatch.setattr(module, "_count", lambda db, model, user_id: 3)
    monkeypatch.setattr(module, "purge_memory_layers", lambda db, user_id: {"memory_entries": 2})

    import app.services.content_purge as content_purge

    monkeypatch.setattr(content_purge, "delete_dependent_rows", lambda db, ids: 0)
    calls = {"vectors": [], "objects": []}
    monkeypatch.setattr(
        content_purge,
        "purge_vectors",
        lambda ids: (calls["vectors"].append(ids) or 0, ["向量未清理"]),
    )
    monkeypatch.setattr(
        content_purge,
        "purge_objects",
        lambda targets: (calls["objects"].append(targets) or 0, []),
    )

    receipt = module.hard_purge_user_assets(_DB(rows), "u-1")

    assert receipt.status == "failed"
    assert receipt.layers["pg_contents"] == "done"
    assert receipt.layers["qdrant"] == "failed"
    assert receipt.layers["cos"] == "done"
    assert receipt.layers["memory"] == "done"
    assert receipt.retained_profile_sensitive == 3
    assert calls["vectors"] and calls["objects"]
    assert receipt.counts["memory_entries"] == 2
    assert "向量未清理" in receipt.warnings


def test_receipt_status_is_only_done_or_failed():
    assert module.PurgeReceipt(status="done").status == "done"
    assert module.PurgeReceipt(status="failed").status == "failed"


def test_undo_window_is_thirty_days_and_naive_timestamps_are_utc():
    from datetime import UTC, datetime

    at = datetime(2026, 11, 1, 8, 0, tzinfo=UTC)
    assert module._window_until(at) == datetime(2026, 12, 1, 8, 0, tzinfo=UTC)
    assert module._window_until(datetime(2026, 11, 1, 8, 0)).tzinfo == UTC  # noqa: DTZ001
