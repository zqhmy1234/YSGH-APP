"""CrisisPort implementation: persist one crisis event, never claim contact.

The table has no "contacted" column by design (B7-04). A write failure returns
``record_status=failed`` so the hotline reply still goes out; a safety reply must
never depend on analytics persistence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.db.models.crisis import CrisisEvent
from app.domain.crisis.scan import CrisisSignal

RecordStatus = Literal["recorded", "failed", "not_applicable"]


@dataclass(frozen=True)
class CrisisRecord:
    level: str
    record_status: RecordStatus
    escalation_note: str = ""


class SqlCrisisPort:
    def __init__(self, db: Session) -> None:
        self._db = db

    def record(self, user_id: str, signal: CrisisSignal) -> CrisisRecord:
        if signal.level == "none":
            return CrisisRecord(level="none", record_status="not_applicable")
        try:
            self._db.add(
                CrisisEvent(
                    user_id=user_id,
                    level=signal.level,
                    trigger=signal.reason[:2000],
                    action="short_circuit",
                    record_status="recorded",
                    resource_given="\n".join(signal.resources)[:2000],
                    escalation_note="agent_not_called",
                )
            )
            self._db.commit()
        except Exception:  # noqa: BLE001 —— 安全回复优先，记录失败不阻断
            self._db.rollback()
            return CrisisRecord(level=signal.level, record_status="failed", escalation_note="db_write_failed")
        return CrisisRecord(level=signal.level, record_status="recorded")


__all__ = ["CrisisRecord", "RecordStatus", "SqlCrisisPort"]
