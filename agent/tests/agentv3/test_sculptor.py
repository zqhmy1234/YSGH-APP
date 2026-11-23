"""Sculptor: propose, validate all 19 codes, apply atomically."""

from agentv3.agents.sculptor import ISSUE_CODES, Sculptor
from agentv3.contracts import MemoryLayer, MemoryProposal, OpKind, ProposalBatch
from agentv3.memory.ledger import MemoryStore


def _sculptor():
    return Sculptor(MemoryStore())


def test_nineteen_issue_codes_are_declared():
    assert len(ISSUE_CODES) == 19


def test_duplicate_text_becomes_came_from_not_a_second_row():
    store = MemoryStore()
    sculptor = Sculptor(store)
    store.commit(ProposalBatch(person_id="p1", batch_hash="hash-0001", proposals=(MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.FACT, text="她喜欢桂花", evidence=("e1",)),)))
    proposals = sculptor.propose_from_episodes("p1", [{"text": "她喜欢桂花", "layer": "fact", "evidence": ["e2"]}])
    assert proposals[0].op is OpKind.CAME_FROM
    assert proposals[0].entry_id is not None


def test_validate_flags_missing_evidence_and_future_date():
    sculptor = _sculptor()
    from datetime import date

    bad = MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.FACT, text="无证据", evidence=(), valid_from=date(2099, 1, 1))
    codes = {code for _, code in sculptor.validate("p1", [bad])}
    assert {"missing_evidence", "valid_from_future"} <= codes


def test_validate_flags_unknown_evidence_and_bad_targets():
    sculptor = _sculptor()
    codes = {
        code
        for _, code in sculptor.validate(
            "p1",
            [
                MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.FACT, text="x", evidence=("zzz",)),
                MemoryProposal(op=OpKind.UPDATE, layer=MemoryLayer.FACT, text="y", entry_id=None),
                MemoryProposal(op=OpKind.RETRACT, layer=MemoryLayer.FACT, text="z", entry_id=None),
            ],
            allowed_evidence={"e1"},
        )
    }
    assert {"unknown_evidence", "update_target_missing", "retract_target_missing"} <= codes


def test_apply_refuses_batch_on_any_issue():
    store = MemoryStore()
    sculptor = Sculptor(store)
    batch = ProposalBatch(
        person_id="p1",
        batch_hash="hash-0002",
        proposals=(MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.FACT, text="好的", evidence=("e1",)),
                   MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.FACT, text="坏的", evidence=())),
    )
    receipt = sculptor.apply(batch)
    assert receipt.rolled_back and receipt.committed == ()
    assert store.entries("p1") == []


def test_apply_commits_clean_batch():
    store = MemoryStore()
    sculptor = Sculptor(store)
    batch = ProposalBatch(
        person_id="p1",
        batch_hash="hash-0003",
        proposals=(MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.FACT, text="她喜欢桂花", evidence=("e1",)),),
    )
    receipt = sculptor.apply(batch)
    assert receipt.committed and len(store.entries("p1")) == 1
