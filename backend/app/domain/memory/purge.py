"""B5-04 purge: four layers, one receipt, retryable.

Order matters: PG content rows are committed first, then Qdrant vectors and COS
objects are best-effort cleaned, and only then the memory schema rows. A failure
returns ``failed`` with the layer that did not finish; rerunning is idempotent
because every delete is a predicate, not a counter-driven loop.

``profile_sensitive`` is protective data and is deliberately retained; the
receipt reports how many rows were kept.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AuditLog,
    ConsentGrant,
    Content,
    ContentPerson,
    EchoExclusion,
    EchoHistory,
    EchoPreference,
    Person,
    ProfileSensitive,
    User,
    VaultEntry,
)
from app.db.models.memory import (
    MemoryBatch,
    MemoryChangeLog,
    MemoryCorrection,
    MemoryDraft,
    MemoryEntry,
    MemoryFactClaim,
)
from app.services.query_helpers import alive_clause

PurgeStatus = Literal["done", "failed"]
LayerName = Literal["pg_contents", "qdrant", "cos", "memory"]
GRACE_DAYS = 30


@dataclass
class PurgeReceipt:
    status: PurgeStatus
    layers: dict[str, str] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    retained_profile_sensitive: int = 0
    warnings: list[str] = field(default_factory=list)
    undo_until: datetime | None = None
    restored: bool = False


def _window_until(deleted_at: datetime) -> datetime:
    if deleted_at.tzinfo is None:
        deleted_at = deleted_at.replace(tzinfo=UTC)
    return deleted_at + timedelta(days=GRACE_DAYS)


def account_undo_until(deleted_at: datetime) -> datetime:
    """Public 30-day boundary shared by the domain and the auth dependency."""
    return _window_until(deleted_at)


def _account_key(user_id: str) -> str:
    return hashlib.sha256(f"account-purge:{user_id}".encode()).hexdigest()


def _batch_receipt(db: Session, user_id: str) -> dict | None:
    row = db.execute(
        select(MemoryBatch).where(MemoryBatch.batch_hash == _account_key(user_id))
    ).scalar_one_or_none()
    return dict(row.receipt or {}) if row is not None else None


def _set_batch_receipt(db: Session, user_id: str, receipt: dict) -> None:
    row = db.execute(
        select(MemoryBatch).where(MemoryBatch.batch_hash == _account_key(user_id))
    ).scalar_one_or_none()
    if row is None:
        db.add(MemoryBatch(batch_hash=_account_key(user_id), receipt=receipt))
    else:
        row.receipt = receipt


def _count(db: Session, model, user_id: str) -> int:
    return int(db.execute(select(func.count()).select_from(model).where(model.user_id == user_id)).scalar() or 0)


def purge_memory_layers(db: Session, user_id: str) -> dict[str, int]:
    """Delete B5 memory/derived rows for one user; never touches profile_sensitive."""
    counts: dict[str, int] = {}
    for name, model in (
        ("memory_drafts", MemoryDraft),
        ("memory_entries", MemoryEntry),
        ("memory_fact_claims", MemoryFactClaim),
        ("memory_corrections", MemoryCorrection),
        ("memory_change_log", MemoryChangeLog),
    ):
        result = db.execute(delete(model).where(model.user_id == user_id))
        counts[name] = int(result.rowcount or 0)
    result = db.execute(delete(ContentPerson).where(ContentPerson.person_id.in_(
        select(Person.id).where(Person.user_id == user_id)
    )))
    counts["content_persons"] = int(result.rowcount or 0)
    result = db.execute(delete(Person).where(Person.user_id == user_id))
    counts["persons"] = int(result.rowcount or 0)
    for name, model in (
        ("echo_exclusions", EchoExclusion),
        ("echo_preference", EchoPreference),
        ("echo_history", EchoHistory),  # 2026-11-24 四路审判：划掉/展示历史是可关联个人的行为数据
        ("consent_grant", ConsentGrant),
        ("vault_entry", VaultEntry),
        ("audit_log", AuditLog),
    ):
        result = db.execute(delete(model).where(model.user_id == user_id))
        counts[name] = int(result.rowcount or 0)
    return counts


def hard_purge_user_assets(db: Session, user_id: str) -> PurgeReceipt:
    """Full B5 delete: contents -> vectors/objects -> memory schema.

    Account-level path; callers must already have authorization and must pass
    the user's own id (the query is user-scoped). User-facing deletion only
    reaches this through ``purge_due_account`` after the 30-day undo window;
    ``workers/cleanup_job.run_account_purges`` is the scheduled entry.
    """
    receipt = PurgeReceipt(status="done")
    retained = _count(db, ProfileSensitive, user_id)
    # 1) snapshot object keys before deleting rows; ORM rows are unreadable after.
    rows = db.execute(
        select(Content.id, Content.cos_key, Content.thumbnail_key).where(Content.user_id == user_id)
    ).all()
    content_ids = [str(row[0]) for row in rows]
    targets = [(str(row[0]), row[1] or "", row[2] or "") for row in rows]

    try:
        from app.services.content_purge import delete_dependent_rows, purge_objects, purge_vectors

        delete_dependent_rows(db, content_ids)
        db.execute(delete(Content).where(Content.user_id == user_id))
        db.commit()
        receipt.layers["pg_contents"] = "done"
        receipt.counts["contents"] = len(content_ids)

        vec_ok, vec_warn = purge_vectors(content_ids)
        receipt.counts["vectors"] = int(vec_ok)
        if vec_warn:
            receipt.warnings.extend(vec_warn)
            receipt.layers["qdrant"] = "failed"
        else:
            receipt.layers["qdrant"] = "done"

        obj_ok, obj_warn = purge_objects(targets)
        receipt.counts["objects"] = int(obj_ok)
        if obj_warn:
            receipt.warnings.extend(obj_warn)
            receipt.layers["cos"] = "failed"
        else:
            receipt.layers["cos"] = "done"

        # 2) memory schema last: a failed external cleanup must not erase the
        #    ledger that lets a retry find what still needs cleaning.
        receipt.counts.update(purge_memory_layers(db, user_id))
        db.commit()
        receipt.layers["memory"] = "done"
    except Exception as exc:  # noqa: BLE001 —— 状态只允许 done/failed，异常也要回执
        db.rollback()
        receipt.status = "failed"
        receipt.warnings.append(f"purge_failed:{type(exc).__name__}")
        for layer in ("pg_contents", "qdrant", "cos", "memory"):
            receipt.layers.setdefault(layer, "failed")
    else:
        if any(value == "failed" for value in receipt.layers.values()):
            receipt.status = "failed"
    receipt.retained_profile_sensitive = retained
    return receipt


__all__ = [
    "PurgeReceipt",
    "PurgeStatus",
    "account_undo_until",
    "hard_purge_user_assets",
    "purge_due_account",
    "purge_memory_layers",
    "request_account_purge",
    "restore_account_purge",
]


def request_account_purge(
    db: Session,
    user_id: str,
    *,
    actor_id: str | None = None,
    now: datetime | None = None,
) -> PurgeReceipt:
    """Start the 30-day undo window. Nothing is physically deleted here.

    Contents are soft-deleted through the single ``sync_writes.soft_delete_content``
    entry (tombstone + deleted_logs + change log), so the existing trash/restore
    flow keeps working. Memory rows and ``profile_sensitive`` stay intact until
    ``purge_due_account`` runs after the window.
    """
    now = now or datetime.now(UTC)
    receipt = PurgeReceipt(status="done")
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None:
        receipt.status = "failed"
        receipt.warnings.append("user_not_found")
        return receipt
    if user.deleted_at is not None:
        receipt.undo_until = _window_until(user.deleted_at)
        receipt.counts["contents"] = 0
        receipt.layers.update(
            {"pg_contents": "already_pending", "qdrant": "pending", "cos": "pending", "memory": "pending"}
        )
        return receipt
    try:
        from app.services import sync_writes

        rows = db.execute(
            select(Content).where(Content.user_id == user_id, alive_clause(Content))
        ).scalars().all()
        for row in rows:
            sync_writes.soft_delete_content(db, row, actor_id or user_id, change_ts=now, anchor=now)
        user.status = 0
        user.deleted_at = now
        user.deleted_by = actor_id or user_id
        _set_batch_receipt(
            db,
            user_id,
            {
                "kind": "account_purge",
                "content_ids": [str(row.id) for row in rows],
                "requested_at": now.isoformat(),
            },
        )
        db.commit()
        receipt.counts["contents"] = len(rows)
        receipt.undo_until = now + timedelta(days=GRACE_DAYS)
        receipt.layers.update(
            {"pg_contents": "soft_deleted", "qdrant": "pending", "cos": "pending", "memory": "pending"}
        )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        receipt.status = "failed"
        receipt.warnings.append(f"account_purge_failed:{type(exc).__name__}")
    return receipt


def restore_account_purge(
    db: Session,
    user_id: str,
    *,
    now: datetime | None = None,
) -> PurgeReceipt:
    """Undo inside the 30-day window; after it, returns failed without touching data."""
    now = now or datetime.now(UTC)
    receipt = PurgeReceipt(status="done")
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    batch = _batch_receipt(db, user_id)
    if user is None or batch is None or user.deleted_at is None:
        receipt.status = "failed"
        receipt.warnings.append("no_pending_account_purge")
        return receipt
    receipt.undo_until = _window_until(user.deleted_at)
    if now > receipt.undo_until:
        receipt.status = "failed"
        receipt.warnings.append("undo_window_expired")
        return receipt
    try:
        from app.services import sync_writes

        ids = [str(value) for value in (batch.get("content_ids") or [])]
        rows = db.execute(
            select(Content).where(Content.id.in_(ids), Content.deleted_at.is_not(None))
        ).scalars().all()
        for row in rows:
            sync_writes.restore_content(db, row, user_id, when=now)
        user.status = 1
        user.deleted_at = None
        user.deleted_by = None
        batch["restored_at"] = now.isoformat()
        _set_batch_receipt(db, user_id, batch)
        db.commit()
        receipt.restored = True
        receipt.counts["contents"] = len(rows)
        receipt.layers.update(
            {"pg_contents": "restored", "qdrant": "pending", "cos": "pending", "memory": "pending"}
        )
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        receipt.status = "failed"
        receipt.warnings.append(f"account_restore_failed:{type(exc).__name__}")
    return receipt


def purge_due_account(
    db: Session,
    user_id: str,
    *,
    now: datetime | None = None,
) -> PurgeReceipt:
    """Cleanup job entry: only hard-deletes after the 30-day window."""
    now = now or datetime.now(UTC)
    user = db.execute(select(User).where(User.id == user_id)).scalar_one_or_none()
    if user is None:
        return PurgeReceipt(status="failed", warnings=["user_not_found"])
    if user.deleted_at is None:
        return PurgeReceipt(status="failed", warnings=["not_pending"])
    until = _window_until(user.deleted_at)
    if now <= until:
        return PurgeReceipt(
            status="done",
            layers={"pg_contents": "soft_deleted", "qdrant": "pending", "cos": "pending", "memory": "pending"},
            undo_until=until,
        )
    batch = _batch_receipt(db, user_id)
    if batch and batch.get("purged_at"):
        return PurgeReceipt(
            status="done",
            layers={"pg_contents": "purged", "qdrant": "purged", "cos": "purged", "memory": "purged"},
            undo_until=until,
            warnings=["already_purged"],
        )
    receipt = hard_purge_user_assets(db, user_id)
    if receipt.status == "done":
        batch = batch or {"kind": "account_purge"}
        batch["purged_at"] = now.isoformat()
        _set_batch_receipt(db, user_id, batch)
        db.commit()
        receipt.layers["account"] = "purged"
    receipt.undo_until = until
    return receipt
