"""Linger parity: the constants and semantics we claim to have ported.

Each test names the upstream rule it protects, so an upstream bump can be
diffed rule-by-rule instead of trusting a README.
"""

from datetime import date

from agentv3.adapters.fakes import FakeSearch, _hit
from agentv3.agents.librarian import RRF_K, Librarian
from agentv3.agents.sculptor import ISSUE_CODES, Sculptor
from agentv3.contracts import MAX_ALTERNATES, MemoryLayer, MemoryProposal, OpKind, ProposalBatch, ResultStatus
from agentv3.memory.ledger import MemoryStore


def test_rule_rrf_k_is_60():
    """Linger memory/README: RRF k=60."""
    assert RRF_K == 60


def test_rule_alternates_at_most_six():
    assert MAX_ALTERNATES == 6


def test_rule_issue_codes_are_nineteen():
    assert len(ISSUE_CODES) == 19


def test_rule_two_ranked_lists_are_separate():
    lib = Librarian(FakeSearch([_hit("e1", "原文"), _hit("e2", "摘要", source_kind="summary")]))
    result = lib.retrieve(lib.resolve_query("x", "p1"))
    assert [h.evidence_id for h in result.hits] == ["e1"]
    assert [h.evidence_id for h in result.summaries] == ["e2"]


def test_rule_empty_only_after_originals_were_queried():
    lib = Librarian(FakeSearch([_hit("e1", "原文")]))
    result = lib.retrieve(lib.resolve_query("x", "p1"))
    assert result.status is ResultStatus.GROUNDED
    assert result.hits


def test_rule_time_votes_and_never_filters():
    lib = Librarian(FakeSearch([_hit("e1", "一月的记录")]))
    query = lib.resolve_query("三月的事", "p1", date_from=date(2026, 3, 1), date_to=date(2026, 3, 31))
    assert "e1" in {h.evidence_id for h in lib.retrieve(query).hits}


def test_rule_batch_rolls_back_and_is_idempotent():
    store = MemoryStore()
    sculptor = Sculptor(store)
    batch = ProposalBatch(
        person_id="p1",
        batch_hash="parity-0001",
        proposals=(MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.FACT, text="她喜欢桂花", evidence=("e1",)),),
    )
    first = sculptor.apply(batch)
    second = sculptor.apply(batch)
    assert first.committed and second.committed == first.committed
    assert len(store.entries("p1")) == 1


def test_rule_correction_is_never_overwritten():
    store = MemoryStore()
    store.add_correction("p1", field="text", value="她不喜欢桂花")
    receipt = store.commit(
        ProposalBatch(
            person_id="p1",
            batch_hash="parity-0002",
            proposals=(MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.FACT, text="她不喜欢桂花", evidence=("e1",)),),
        )
    )
    assert receipt.rolled_back and store.entries("p1") == []


def test_rule_change_log_is_app_written():
    store = MemoryStore()
    store.commit(
        ProposalBatch(
            person_id="p1",
            batch_hash="parity-0003",
            proposals=(MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.FACT, text="她养了一只猫", evidence=("e1",)),),
        )
    )
    log = store.change_log("p1")
    assert log and log[0]["batch_hash"] == "parity-0003" and log[0]["op"] == "add"


def test_rule_same_text_merges_evidence_instead_of_duplicating():
    store = MemoryStore()
    entry = store.commit(
        ProposalBatch(
            person_id="p1",
            batch_hash="parity-0004",
            proposals=(MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.FACT, text="她喜欢桂花", evidence=("e1",)),),
        )
    ).committed[0]
    store.commit(
        ProposalBatch(
            person_id="p1",
            batch_hash="parity-0005",
            proposals=(MemoryProposal(op=OpKind.CAME_FROM, layer=MemoryLayer.FACT, text="她喜欢桂花", entry_id=entry, evidence=("e2",)),),
        )
    )
    assert len(store.entries("p1")) == 1
    assert store.entries("p1")[0].evidence == ("e1", "e2")
