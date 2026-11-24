"""B5-01 probes: no evidence never writes; dual-write switch is honest."""

from __future__ import annotations

import uuid

from app.domain.memory.ledger import MemoryWritePort, evidence_ok


class FakeOldStore:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def find_duplicate(self, user_id: str, text: str) -> dict | None:
        return next((r for r in self.rows if r["user_id"] == user_id and r["text"] == text), None)

    def insert(self, user_id: str, text: str, summary: str) -> dict:
        row = {"id": len(self.rows) + 1, "user_id": user_id, "text": text, "summary": summary}
        self.rows.append(row)
        return row


class FakeShadowStore:
    def __init__(self) -> None:
        self.rows: dict[str, dict] = {}
        self.fail_next = False

    def get(self, entry_id: str) -> dict | None:
        return self.rows.get(entry_id)

    def insert(self, entry_id: str, payload: dict) -> None:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("shadow down")
        self.rows[entry_id] = payload


def _evidence() -> list[dict]:
    return [{"type": "content", "id": str(uuid.uuid4()), "quote": "原话"}]


def _port(*, enabled: bool, old: FakeOldStore, shadow: FakeShadowStore) -> MemoryWritePort:
    return MemoryWritePort(dual_write_enabled=enabled, old_store=old, shadow_store=shadow)


def test_evidence_requires_type_and_id():
    assert evidence_ok([]) is False
    assert evidence_ok([{"type": "", "id": "x"}]) is False
    assert evidence_ok([{"type": "content", "id": ""}]) is False
    assert evidence_ok(_evidence()) is True


def test_missing_evidence_is_rejected_without_any_row():
    old, shadow = FakeOldStore(), FakeShadowStore()
    result = _port(enabled=True, old=old, shadow=shadow).write(
        user_id="u1",
        text="这条不该落库",
        evidence=[],
        idempotency_key=f"probe-{uuid.uuid4()}",
    )
    assert result.receipt.state == "rejected"
    assert old.rows == [] and shadow.rows == {}


def test_switch_off_writes_old_only():
    old, shadow = FakeOldStore(), FakeShadowStore()
    result = _port(enabled=False, old=old, shadow=shadow).write(
        user_id="u1",
        text="只写旧表",
        evidence=_evidence(),
        idempotency_key="off",
    )
    assert result.receipt.state == "draft"
    assert result.receipt.reason == "shadow_write_disabled"
    assert len(old.rows) == 1
    assert shadow.rows == {}


def test_switch_on_writes_both_rows():
    old, shadow = FakeOldStore(), FakeShadowStore()
    result = _port(enabled=True, old=old, shadow=shadow).write(
        user_id="u1",
        text="今天在西湖边坐了很久，想起小时候的夏天。",
        evidence=_evidence(),
        idempotency_key="on",
    )
    assert result.receipt.state == "draft"
    assert result.entry_id in shadow.rows
    assert len(old.rows) == 1


def test_shadow_failure_keeps_old_write():
    old, shadow = FakeOldStore(), FakeShadowStore()
    shadow.fail_next = True
    result = _port(enabled=True, old=old, shadow=shadow).write(
        user_id="u1",
        text="影子写失败也要保住旧表",
        evidence=_evidence(),
        idempotency_key="shadow",
    )
    assert result.shadow_failed is True
    assert result.receipt.reason.startswith("shadow_write_failed:")
    assert len(old.rows) == 1
    assert shadow.rows == {}


def test_retry_is_idempotent_when_shadow_row_exists():
    old, shadow = FakeOldStore(), FakeShadowStore()
    port = _port(enabled=True, old=old, shadow=shadow)
    kwargs = dict(user_id="u1", text="同一条", evidence=_evidence(), idempotency_key="same")
    first = port.write(**kwargs)
    second = port.write(**kwargs)
    assert first.receipt.idempotent is False
    assert second.receipt.idempotent is True
    assert second.receipt.state == "committed"
    assert len(old.rows) == 1 and len(shadow.rows) == 1
