"""Eight north-star probes: each one must fail if the protection is removed.

The judge replay (``judge/probes.py``) weakens the implementation, expects these
to go red, then restores and expects green. Here they simply assert the good state.
"""

from datetime import datetime

import pytest

from agentv3.adapters.fakes import FakeClock, FakeEvents, FakeLLM, FakeSearch, _hit
from agentv3.agents.librarian import Librarian
from agentv3.agents.muse import Muse
from agentv3.agents.provenance import Provenance
from agentv3.agents.sculptor import Sculptor
from agentv3.contracts import (
    AgentName,
    Claim,
    ClaimBundle,
    Digest,
    DigestSection,
    MemoryLayer,
    MemoryProposal,
    OpKind,
    ProposalBatch,
    ResultStatus,
    SearchResult,
    TurnContext,
    TurnRequest,
    VerdictStatus,
)
from agentv3.frame import build_frame
from agentv3.memory.ledger import MemoryStore
from agentv3.memory.policy import Policy
from agentv3.orchestrator import Orchestrator
from agentv3.plan import plan
from agentv3.registry import assert_can_write


def _orch(llm=None, search=None, store=None):
    return Orchestrator(
        llm=llm or FakeLLM(["我在听。"]),
        search=search or FakeSearch([_hit("e1", "西湖")]),
        events=FakeEvents(),
        store=store or MemoryStore(),
        clock=FakeClock(datetime(2026, 11, 23, 10, 0)),
    )


def test_probe_1_private_claim_without_citation_is_not_passed():
    verdict = Provenance(Policy()).verify(ClaimBundle(claims=(Claim(text="她去年住在城西", kind="private"),)), ())
    assert verdict.status is VerdictStatus.NEEDS_EVIDENCE


def test_probe_2_sculptor_cannot_override_user_correction():
    store = MemoryStore()
    store.add_correction("p1", field="text", value="她喜欢桂花")
    receipt = store.commit(
        ProposalBatch(
            person_id="p1",
            batch_hash="probe-0002",
            proposals=(MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.FACT, text="她喜欢桂花", evidence=("e1",)),),
        )
    )
    assert receipt.rolled_back and store.entries("p1") == []


def test_probe_3_boundary_is_never_pruned():
    context = TurnContext(request=TurnRequest(person_id="p1", text="上次去杭州"), now=datetime(2026, 11, 23))
    frame = build_frame(
        plan=plan(context),
        persona="陪" * 1000,
        relation="老" * 200,
        boundaries=["逝者" + "边" * 148, "葬礼" + "界" * 148],
        corrections=["她叫小林" + "正" * 195, "她住城西" + "修" * 195],
        digest=Digest(person_id="p1", sections=(DigestSection(name="身份", text="x" * 600),)),
        narrative="n" * 300,
        evidence=SearchResult(hits=(_hit("e1", "西湖"),)),
        crisis_note="危" * 200,
    )
    kept = {s.name: s.text for s in frame.segments}
    assert kept["boundaries"] and kept["corrections"]


def test_probe_4_empty_does_not_carry_three_meanings():
    lib_bad = Librarian(FakeSearch([], down=("dense",)))
    degraded = lib_bad.retrieve(lib_bad.resolve_query("x", "p1"))
    lib = Librarian(FakeSearch([]))
    no_match = lib.retrieve(lib.resolve_query("x", "p1"))
    assert degraded.gaps and not no_match.gaps
    assert no_match.reason == "no_match"


def test_probe_5_degraded_turn_never_asks_a_clarify():
    lib = Librarian(FakeSearch([], down=("keyword", "dense", "sparse", "time")))
    result = lib.retrieve(lib.resolve_query("那次", "p1"))
    assert result.clarify is None


def test_probe_6_double_nightly_run_is_idempotent():
    store = MemoryStore()
    sculptor = Sculptor(store)
    batch = ProposalBatch(
        person_id="p1",
        batch_hash="probe-0006",
        proposals=(MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.FACT, text="她养了一只猫", evidence=("e1",)),),
    )
    sculptor.apply(batch)
    second = sculptor.apply(batch)
    assert len(store.entries("p1")) == 1 and second.idempotent


def test_probe_7_muse_holds_no_write_handle():
    with pytest.raises(PermissionError):
        assert_can_write(AgentName.MUSE)
    assert Muse(FakeLLM(["我在听"]))._tools == {}


def test_probe_8_never_says_no_record_without_querying_originals():
    result = _orch().run(TurnRequest(person_id="p1", text="上次去哪了"))
    assert result.status is not ResultStatus.EMPTY
    assert result.trace
