"""B5 merge: Agent writes canonical memory via backend, not a local JSON store."""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from adapters.backend_http import BackendHTTP  # noqa: E402
from adapters.backend_memory import BackendMemoryStore  # noqa: E402
from harness.contracts import (  # noqa: E402
    MemoryLayer,
    MemoryProposal,
    OpKind,
    ProposalBatch,
)
from ports import PortUnavailable  # noqa: E402


class _FakeBackendHTTP(BackendHTTP):
    def __init__(self):
        super().__init__(base_url="http://x", token="t", user_id="p1")  # noqa: S106 - test stub
        self.writes: list[dict] = []
        self.fail = False
        self.snapshot = {"status": "ok", "narrative": []}
        self.deleted: list[str] = []

    def write_memory(self, user_id, payload):
        if self.fail:
            raise PortUnavailable("backend", "down")
        self.writes.append({"user_id": user_id, **payload})
        return {"entry_id": payload["entry_id"], "status": "ok"}

    def memory(self, user_id):
        return self.snapshot

    def delete_memory(self, user_id, entry_id):
        self.deleted.append(entry_id)
        return {"data": {"deleted": True}}


def _batch(text: str = "一段新记忆") -> ProposalBatch:
    return ProposalBatch(
        batch_hash="bh-merge-test",
        person_id="p1",
        proposals=(
            MemoryProposal(
                op=OpKind.ADD,
                layer=MemoryLayer.NARRATIVE,
                text=text,
                evidence=("e1",),
                reason="test",
            ),
        ),
    )


def test_commit_writes_to_backend_not_local_json():
    backend = _FakeBackendHTTP()
    store = BackendMemoryStore(backend)
    receipt = store.commit(_batch())
    assert not receipt.rejected
    assert len(backend.writes) == 1
    w = backend.writes[0]
    assert w["user_id"] == "p1"
    assert w["text"] == "一段新记忆"
    assert w["entry_id"].startswith("m_")
    assert w["evidence"] == [{"type": "agent_evidence", "id": "e1"}]


def test_commit_is_idempotent_no_duplicate_backend_write():
    backend = _FakeBackendHTTP()
    store = BackendMemoryStore(backend)
    batch = _batch()
    first = store.commit(batch)
    second = store.commit(batch)
    assert first.committed == second.committed
    assert second.idempotent is True
    assert len(backend.writes) == 1, "重试不得在后端写第二条"


def test_backend_failure_rolls_back_in_memory_and_batch_marker():
    backend = _FakeBackendHTTP()
    store = BackendMemoryStore(backend)
    backend.fail = True
    with pytest.raises(PortUnavailable):
        store.commit(_batch())
    assert store.entries("p1") == [], "后端写失败不得在内存里留下未落库的记忆"
    # 恢复后端后重试应能成功（batch 标记已清除）
    backend.fail = False
    receipt = store.commit(_batch())
    assert not receipt.rejected and len(backend.writes) == 1


def test_hydrate_loads_backend_narrative_for_dedup():
    backend = _FakeBackendHTTP()
    backend.snapshot = {
        "status": "ok",
        "narrative": [
            {
                "entry_id": "m_000000000001",
                "text": "历史记忆",
                "evidence": ["e0"],
                "valid_from": "2026-01-01",
                "story_arc": "",
            }
        ],
    }
    store = BackendMemoryStore(backend)
    assert store.hydrate("p1") == 1
    assert "历史记忆" in [e.text for e in store.entries("p1")]
    # hydrated 的 entry 已标记 synced：hydrate 后再 commit 不会重复写它们
    store.commit(_batch("新记忆"))
    assert all(w["text"] != "历史记忆" for w in backend.writes)


def test_hydrate_unavailable_returns_zero_without_claims():
    backend = _FakeBackendHTTP()
    backend.snapshot = {"status": "unavailable", "note": "consent_not_granted:personalization"}
    store = BackendMemoryStore(backend)
    assert store.hydrate("p1") == 0
    assert store.entries("p1") == []


def test_write_payload_is_schema_compatible():
    """payload 字段必须匹配 backend MemoryWriteIn（entry_id m_<12hex>、kind 白名单）。"""
    backend = _FakeBackendHTTP()
    store = BackendMemoryStore(backend)
    store.commit(_batch())
    w = backend.writes[0]
    import re

    assert re.fullmatch(r"m_[0-9a-f]{12}", w["entry_id"])
    assert w["kind"] in {"episode", "impression", "story_segment", "emotion_trace"}
    assert w["layer"] in {"narrative", "fact_claim"}
    assert w["sensitivity"] in {"normal", "sensitive", "forbidden"}
    assert isinstance(date.fromisoformat(w["valid_from"]), date)


def test_propose_writes_through_without_nightly():
    """[NAIL] 审判 P0-1：生产路径只调 propose，旧实现返回 DRAFT 永不落库。"""
    from harness.contracts import MemoryLayer, MemoryProposal, OpKind, WriteState

    backend = _FakeBackendHTTP()
    store = BackendMemoryStore(backend)
    prop = MemoryProposal(
        op=OpKind.ADD, layer=MemoryLayer.NARRATIVE, text="对话自动记忆", evidence=("e1",), reason="turn_capture"
    )
    receipt = store.propose(prop, person_id="p1")
    assert receipt.state is WriteState.CONFIRMED, f"propose 必须直写 canonical，实际 {receipt}"
    assert len(backend.writes) == 1 and backend.writes[0]["text"] == "对话自动记忆"


@pytest.mark.parametrize(
    "layer,expected",
    [
        ("narrative", "narrative"),
        ("fact", "fact_claim"),
        ("preference", "narrative"),
        ("asset", "narrative"),
    ],
)
def test_layer_mapping_matches_backend_enum(layer, expected):
    """[NAIL] 审判 P0-2：agent 四层 → backend 两层，否则非 narrative 整批 422。"""
    from harness.contracts import MemoryLayer, MemoryProposal, OpKind

    backend = _FakeBackendHTTP()
    store = BackendMemoryStore(backend)
    prop = MemoryProposal(op=OpKind.ADD, layer=MemoryLayer(layer), text=f"{layer}记忆", evidence=("e1",), reason="test")
    store.propose(prop, person_id="p1")
    assert backend.writes[0]["layer"] == expected
    assert backend.writes[0]["layer"] in {"narrative", "fact_claim"}


def test_purge_deletes_backend_rows_no_resurrect():
    """[NAIL] 审判 P0-3：purge 不回写后端 → 下次 hydrate 复活已删记忆。"""
    from harness.contracts import MemoryLayer, MemoryProposal, OpKind, PurgeScope

    backend = _FakeBackendHTTP()
    store = BackendMemoryStore(backend)
    prop = MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.NARRATIVE, text="待删除", evidence=("e1",), reason="test")
    store.propose(prop, person_id="p1")
    assert len(backend.writes) == 1
    eid = backend.writes[0]["entry_id"]
    backend.deleted = []
    store.purge(PurgeScope(person_id="p1", layers=(MemoryLayer.NARRATIVE,), entry_ids=()))
    assert eid in backend.deleted, "purge 必须删后端行，否则 hydrate 会复活"
    # hydrate 不再返回该条
    backend.snapshot = {"status": "ok", "narrative": []}
    assert store.hydrate("p1") == 0


def test_rejected_backend_write_is_not_silently_success():
    """[NAIL] 审判 P1-1：后端返回 rejected 时不能当成功。"""
    from harness.contracts import MemoryLayer, MemoryProposal, OpKind
    from ports import PortUnavailable

    class _RejectBackend(_FakeBackendHTTP):
        def write_memory(self, user_id, payload):
            return {"data": {"status": "rejected", "reason": "missing_evidence"}}

    backend = _RejectBackend()
    store = BackendMemoryStore(backend)
    prop = MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.NARRATIVE, text="会被拒", evidence=("e1",), reason="test")
    with pytest.raises(PortUnavailable):
        store.propose(prop, person_id="p1")


def test_entry_id_stable_across_processes():
    """[NAIL] 审判 P1-2：entry_id 必须确定性派生，否则重试在后端写重复行。"""
    from harness.contracts import MemoryLayer, MemoryProposal, OpKind

    ids = []
    for _ in range(2):
        backend = _FakeBackendHTTP()
        store = BackendMemoryStore(backend)
        prop = MemoryProposal(
            op=OpKind.ADD, layer=MemoryLayer.NARRATIVE, text="同一句话", evidence=("e1",), reason="test"
        )
        store.propose(prop, person_id="p1")
        ids.append(backend.writes[0]["entry_id"])
    assert ids[0] == ids[1], f"跨进程重试 entry_id 不稳定：{ids}"
