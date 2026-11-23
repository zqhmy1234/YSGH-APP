"""Memory domain: ledger rules that never touch a database."""

from app.domain.memory.ledger import MemoryWritePort, MemoryWriteResult, evidence_ok
from app.domain.memory.read_model import build_snapshot, read_snapshot, unavailable_snapshot
from app.domain.memory.reconcile import ReconcileReport, reconcile_texts

__all__ = [
    "MemoryWritePort",
    "MemoryWriteResult",
    "ReconcileReport",
    "build_snapshot",
    "evidence_ok",
    "read_snapshot",
    "reconcile_texts",
    "unavailable_snapshot",
]
