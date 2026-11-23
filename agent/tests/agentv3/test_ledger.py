"""Ledger: double time, correction precedence, batch idempotency, rollback, purge."""

from datetime import date, timedelta

import pytest

from agentv3.contracts import MemoryLayer, MemoryProposal, OpKind, ProposalBatch, PurgeScope, WriteState
from agentv3.adapters.sqlite_memory import SqliteMemoryStore
from agentv3.memory.ledger import MemoryStore


@pytest.fixture()
def store():
    s = MemoryStore()
    yield s
    s.close()


def _add(text="她喜欢桂花", layer=MemoryLayer.FACT, evidence=("e1",), entry_id=None):
    return MemoryProposal(op=OpKind.ADD, layer=layer, text=text, evidence=evidence, entry_id=entry_id)


def _batch(store, person_id="p1", proposals=(), h="hash-0001"):
    return store.commit(ProposalBatch(person_id=person_id, batch_hash=h, proposals=tuple(proposals)))


def test_propose_never_confirms(store):
    receipt = store.propose(_add(), person_id="p1")
    assert receipt.state is WriteState.DRAFT
    assert store.entries("p1") == []


def test_propose_rejects_same_text_as_correction(store):
    store.add_correction("p1", field="text", value="她喜欢桂花")
    receipt = store.propose(_add(), person_id="p1")
    assert receipt.state is WriteState.REJECTED and receipt.reason == "correction_precedence"


def test_propose_rejects_fact_without_evidence(store):
    receipt = store.propose(_add(evidence=()), person_id="p1")
    assert receipt.state is WriteState.REJECTED and receipt.reason == "missing_evidence"


def test_commit_add_then_idempotent(store):
    first = _batch(store, proposals=[_add()])
    second = _batch(store, proposals=[_add()])
    assert len(first.committed) == 1 and not first.idempotent
    assert second.idempotent and second.committed == first.committed
    assert len(store.entries("p1")) == 1


def test_update_closes_old_row_and_links_chain(store):
    entry = _batch(store, proposals=[_add()]).committed[0]
    _batch(
        store,
        h="hash-0002",
        proposals=[MemoryProposal(op=OpKind.UPDATE, layer=MemoryLayer.FACT, text="她喜欢白茶", entry_id=entry, evidence=("e2",))],
    )
    history = store.history(entry)
    assert len(history) == 2
    assert history[0].valid_to is not None and history[0].superseded_by == history[1].entry_id
    assert store.entries("p1")[0].text == "她喜欢白茶"


def test_retract_removes_from_active_view(store):
    entry = _batch(store, proposals=[_add()]).committed[0]
    _batch(store, h="hash-0003", proposals=[MemoryProposal(op=OpKind.RETRACT, layer=MemoryLayer.FACT, text="她喜欢桂花", entry_id=entry, evidence=("e3",))])
    assert store.entries("p1") == []
    assert len(store.history(entry)) == 1


def test_came_from_only_adds_evidence(store):
    entry = _batch(store, proposals=[_add()]).committed[0]
    _batch(store, h="hash-0004", proposals=[MemoryProposal(op=OpKind.CAME_FROM, layer=MemoryLayer.FACT, text="她喜欢桂花", entry_id=entry, evidence=("e2",))])
    assert store.entries("p1")[0].evidence == ("e1", "e2")


def test_conflict_rolls_back_whole_batch(store):
    store.add_correction("p1", field="text", value="她喜欢桂花")
    receipt = _batch(store, proposals=[_add(text="别的"), _add(text="她喜欢桂花")])
    assert receipt.rolled_back and receipt.committed == ()
    assert store.entries("p1") == []


def test_change_log_is_written_by_app(store):
    _batch(store, proposals=[_add()])
    rows = store.change_log("p1")
    assert rows and rows[0]["op"] == "add" and rows[0]["batch_hash"] == "hash-0001"


def test_purge_scope(store):
    _batch(store, proposals=[_add(), _add(text="她养了一只猫", evidence=("e9",))], h="hash-0005")
    receipt = store.purge(PurgeScope(person_id="p1", layers=(MemoryLayer.FACT,)))
    assert receipt.purged == 2 and store.entries("p1") == []


def test_persistence_survives_reopen(tmp_path):
    path = str(tmp_path / "memory.db")
    first = SqliteMemoryStore(path)
    first.commit(ProposalBatch(person_id="p1", batch_hash="hash-0006", proposals=(_add(),)))
    first.close()

    second = SqliteMemoryStore(path)
    assert second.entries("p1")[0].text == "她喜欢桂花"
    receipt = second.commit(ProposalBatch(person_id="p1", batch_hash="hash-0006", proposals=(_add(),)))
    assert receipt.idempotent
    second.close()


def test_valid_from_is_recorded_and_ordered(store):
    _batch(store, proposals=[_add(), _add(text="她搬到城西", evidence=("e2",))], h="hash-0007")
    entries = store.entries("p1")
    assert all(e.valid_from <= date.today() + timedelta(days=1) for e in entries)
