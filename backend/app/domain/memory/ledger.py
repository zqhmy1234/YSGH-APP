"""B5-01 write path: one proposal, optional shadow write, honest receipts.

The old ``memories`` table is authoritative during phase B. When the dual-write
switch is off, only the old row is written. When it is on, the canonical
``memory_entries`` row is written inside a savepoint: a shadow-write failure
rolls back *only* the new row, never the old authoritative write.

The canonical entry id is derived from ``(user_id, idempotency_key)``. That makes
retries idempotent without adding a column that is not in the B5 design, and it
keeps the shadow row traceable back to the caller's key.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.memory import MemoryEntry
from app.db.models.memory_agent import Memory
from app.ports.memory import WriteReceipt

_DEDUP_WINDOW_SECONDS = 600


@runtime_checkable
class OldMemoryStore(Protocol):
    """Old authoritative store (phase B)."""

    def find_duplicate(self, user_id: str, text: str) -> Any | None: ...

    def insert(self, user_id: str, text: str, summary: str) -> Any: ...


@runtime_checkable
class ShadowMemoryStore(Protocol):
    """Canonical shadow store (phase B)."""

    def get(self, entry_id: str) -> Any | None: ...

    def insert(self, entry_id: str, payload: dict) -> None: ...



def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def evidence_ok(evidence: tuple[dict, ...] | list[dict]) -> bool:
    """At least one evidence item with a non-empty ``type`` and ``id``."""
    if not evidence:
        return False
    return all(bool(str(item.get("type", "")).strip()) and bool(str(item.get("id", "")).strip()) for item in evidence)


def entry_id_for(user_id: str, idempotency_key: str) -> str:
    digest = hashlib.sha256(f"{user_id}:{idempotency_key}".encode()).hexdigest()
    return f"m_{digest[:12]}"


@dataclass(frozen=True)
class MemoryWriteResult:
    receipt: WriteReceipt
    old_memory_id: int | str | None = None
    entry_id: str = ""
    shadow_failed: bool = False


class MemoryWritePort:
    """Dual-write adapter for the B-phase (old authoritative, new shadow)."""

    def __init__(
        self,
        *,
        dual_write_enabled: bool = False,
        old_store: OldMemoryStore | None = None,
        shadow_store: ShadowMemoryStore | None = None,
    ) -> None:
        self.dual_write_enabled = dual_write_enabled
        self._old_store = old_store
        self._shadow_store = shadow_store
        if old_store is not None and shadow_store is not None and not dual_write_enabled:
            # The switch is authoritative: a configured shadow store must not
            # silently receive writes while the switch is off.
            self._shadow_store = None

    @classmethod
    def from_env(cls) -> MemoryWritePort:
        return cls(dual_write_enabled=os.getenv("MEMORY_DUAL_WRITE", "0").strip().lower() in {"1", "true", "yes"})

    def write(
        self,
        db: Session | None = None,
        *,
        user_id: str,
        text: str,
        evidence: list[dict] | tuple[dict, ...],
        idempotency_key: str,
        summary: str = "",
        kind: str = "episode",
        story_arc: str = "",
        valid_from: date | None = None,
        thread_id: str | None = None,
        card_hash: str | None = None,
    ) -> MemoryWriteResult:
        if not text.strip() or not evidence_ok(evidence):
            return MemoryWriteResult(
                receipt=WriteReceipt(state="rejected", reason="missing_text_or_evidence"),
            )

        entry_id = entry_id_for(user_id, idempotency_key)
        if self._old_store is not None:
            return self._write_stores(
                user_id=user_id,
                text=text,
                evidence=evidence,
                entry_id=entry_id,
                summary=summary,
                kind=kind,
                story_arc=story_arc,
                valid_from=valid_from,
                thread_id=thread_id,
                card_hash=card_hash,
            )
        if db is None:
            raise TypeError("db is required when no old_store is configured")
        if self.dual_write_enabled:
            existing = db.get(MemoryEntry, entry_id)
            if existing is not None:
                return MemoryWriteResult(
                    receipt=WriteReceipt(state="committed", entry_id=entry_id, idempotent=True),
                    entry_id=entry_id,
                )

        old = self._find_old_duplicate(db, user_id=user_id, text=text)
        if old is None:
            old = Memory(
                user_id=str(user_id),
                content=text,
                summary=summary or text[:120],
                content_format="text",
                content_hash=_content_hash(text),
                dedup_bucket=int(time.time() // _DEDUP_WINDOW_SECONDS),
                importance=3,
            )
            db.add(old)
            db.flush()
        old_id = old.id

        if not self.dual_write_enabled:
            return MemoryWriteResult(
                receipt=WriteReceipt(state="draft", reason="shadow_write_disabled"),
                old_memory_id=old_id,
                entry_id=entry_id,
            )

        try:
            with db.begin_nested():
                db.add(
                    MemoryEntry(
                        entry_id=entry_id,
                        user_id=str(user_id),
                        layer="narrative",
                        kind=kind,
                        text=text,
                        summary=(summary or text[:120])[:120],
                        story_arc=story_arc,
                        valid_from=valid_from or datetime.now(UTC).date(),
                        evidence=list(evidence),
                        confidence=0.6,
                        sensitivity="normal",
                        thread_id=thread_id,
                        card_hash=card_hash,
                        created_by="dual_write",
                    )
                )
                db.flush()
        except Exception as exc:  # noqa: BLE001 —— shadow failure must not kill the old write
            return MemoryWriteResult(
                receipt=WriteReceipt(state="draft", reason=f"shadow_write_failed:{type(exc).__name__}"),
                old_memory_id=old_id,
                entry_id=entry_id,
                shadow_failed=True,
            )

        return MemoryWriteResult(
            receipt=WriteReceipt(state="draft", entry_id=entry_id),
            old_memory_id=old_id,
            entry_id=entry_id,
        )

    def _write_stores(
        self,
        *,
        user_id: str,
        text: str,
        evidence: list[dict] | tuple[dict, ...],
        entry_id: str,
        summary: str,
        kind: str,
        story_arc: str,
        valid_from: date | None,
        thread_id: str | None,
        card_hash: str | None,
    ) -> MemoryWriteResult:
        if self._old_store is None:
            raise TypeError("old_store is required in store mode")
        if self.dual_write_enabled and self._shadow_store is not None:
            existing = self._shadow_store.get(entry_id)
            if existing is not None:
                return MemoryWriteResult(
                    receipt=WriteReceipt(state="committed", entry_id=entry_id, idempotent=True),
                    entry_id=entry_id,
                )
        old = self._old_store.find_duplicate(str(user_id), text)
        if old is None:
            old = self._old_store.insert(str(user_id), text, summary or text[:120])
        old_id = getattr(old, "id", old)

        if not self.dual_write_enabled:
            return MemoryWriteResult(
                receipt=WriteReceipt(state="draft", reason="shadow_write_disabled"),
                old_memory_id=old_id,
                entry_id=entry_id,
            )
        if self._shadow_store is None:
            return MemoryWriteResult(
                receipt=WriteReceipt(state="draft", reason="shadow_store_missing"),
                old_memory_id=old_id,
                entry_id=entry_id,
                shadow_failed=True,
            )

        payload = {
            "entry_id": entry_id,
            "user_id": str(user_id),
            "layer": "narrative",
            "kind": kind,
            "text": text,
            "summary": (summary or text[:120])[:120],
            "story_arc": story_arc,
            "valid_from": valid_from or datetime.now(UTC).date(),
            "evidence": list(evidence),
            "confidence": 0.6,
            "sensitivity": "normal",
            "thread_id": thread_id,
            "card_hash": card_hash,
            "created_by": "dual_write",
        }
        try:
            self._shadow_store.insert(entry_id, payload)
        except Exception as exc:  # noqa: BLE001 —— shadow failure must not kill the old write
            return MemoryWriteResult(
                receipt=WriteReceipt(state="draft", reason=f"shadow_write_failed:{type(exc).__name__}"),
                old_memory_id=old_id,
                entry_id=entry_id,
                shadow_failed=True,
            )
        return MemoryWriteResult(
            receipt=WriteReceipt(state="draft", entry_id=entry_id),
            old_memory_id=old_id,
            entry_id=entry_id,
        )

    @staticmethod
    def _find_old_duplicate(db: Session, *, user_id: str, text: str) -> Memory | None:
        """Catch a retried proposal inside the same dedup window."""
        return db.execute(
            select(Memory).where(
                Memory.user_id == str(user_id),
                Memory.content == text,
            ).order_by(Memory.id.desc()).limit(1)
        ).scalar_one_or_none()


def _content_hash(text: str) -> str:
    normalized = normalize_text(text)[:50]
    return hashlib.sha256(normalized.encode()).hexdigest()


__all__ = [
    "MemoryWritePort",
    "MemoryWriteResult",
    "entry_id_for",
    "evidence_ok",
    "normalize_text",
]
