"""Integration: modules composed the way production wires them.

The point is not to re-test units, but to prove the seams: ingest → nightly → ledger
→ digest → turn; a dead search port degrades honestly; crisis never reaches the LLM.
"""

from datetime import datetime, timedelta

from agentv3.adapters.fakes import FakeClock, FakeEvents, FakeLLM, FakeSearch, _hit
from agentv3.adapters.sqlite_memory import SqliteMemoryStore
from agentv3.contracts import FamilyAction, FamilyRole, ResultStatus, Sensitivity, TurnRequest
from agentv3.domain.echo import EchoPolicy
from agentv3.domain.family import FamilyPolicy
from agentv3.domain.ingest import Ingestor
from agentv3.jobs.nightly import NightlyJob
from agentv3.memory.projection import build as build_digest
from agentv3.orchestrator import Orchestrator


def _wired(tmp_path, *, search_down=(), llm_fail=False):
    store = SqliteMemoryStore(str(tmp_path / "m.db"))
    orchestrator = Orchestrator(
        llm=FakeLLM(["我在听。"], fail=llm_fail),
        search=FakeSearch([_hit("ev1", "西湖的下午")], down=search_down),
        events=FakeEvents(),
        store=store,
        clock=FakeClock(datetime(2026, 11, 23, 10, 0)),
    )
    return store, orchestrator


def test_ingest_nightly_digest_turn_chain(tmp_path):
    store, orchestrator = _wired(tmp_path)
    episodes, _ = Ingestor(today=datetime(2026, 11, 23).date()).ingest(
        "p1",
        [
            {"text": "她喜欢桂花", "day": "2026-11-20", "evidence_id": "ev1", "place": "西湖"},
            {"text": "她养了一只猫", "day": "2026-11-21", "evidence_id": "ev2"},
            {"text": "那天聊到很晚", "day": "2026-11-22", "evidence_id": "ev3", "source": "voice"},
        ],
    )
    receipt = NightlyJob(store).run("p1", [e.model_dump() for e in episodes], day=datetime(2026, 11, 23).date())
    assert len(receipt.committed) == 3

    digest = build_digest(store.entries("p1"), "p1")
    assert digest.state == "ok" and digest.chars <= 600

    result = orchestrator.run(TurnRequest(person_id="p1", text="上次去哪了"))
    assert result.status in {ResultStatus.GROUNDED, ResultStatus.PARTIAL}
    assert result.trace


def test_dead_search_degrades_turn_without_fake_grounding(tmp_path):
    _, orchestrator = _wired(tmp_path, search_down=("keyword", "dense", "sparse", "time"))
    result = orchestrator.run(TurnRequest(person_id="p1", text="上次去哪了"))
    assert result.degraded and result.status is ResultStatus.PARTIAL
    assert result.cites == ()
    assert "search" in result.degraded_ports


def test_crisis_never_reaches_llm_or_store(tmp_path):
    store, orchestrator = _wired(tmp_path)
    result = orchestrator.run(TurnRequest(person_id="p1", text="我不想活了"))
    assert "急救" in result.reply
    assert len(orchestrator._llm.calls) == 0
    assert store.entries("p1") == []


def test_family_lens_over_the_same_rows(tmp_path):
    store, _ = _wired(tmp_path)
    from agentv3.contracts import MemoryLayer, MemoryProposal, OpKind, ProposalBatch

    store.commit(
        ProposalBatch(
            person_id="p1",
            batch_hash="int-0001",
            proposals=(
                MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.FACT, text="她喜欢桂花", evidence=("ev1",)),
                MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.FACT, text="葬礼那天", evidence=("ev2",)),
            ),
        )
    )
    episodes, _ = Ingestor(today=datetime(2026, 11, 23).date()).ingest(
        "p1",
        [
            {"text": "今天去了西湖", "place": "西湖", "evidence_id": "ev1"},
            {"text": "葬礼那天", "place": "家", "evidence_id": "ev2"},
        ],
    )
    families = FamilyPolicy()
    assert not families.can(FamilyRole.CHILD, FamilyAction.DELETE, Sensitivity.PERSONAL).allow
    assert [e.episode_id for e in families.visible(FamilyRole.CHILD, episodes)] == [episodes[0].episode_id]
    assert len(families.visible(FamilyRole.OWNER, episodes)) == 2


def test_echo_reads_the_same_store_without_writing(tmp_path):
    store, _ = _wired(tmp_path)
    episodes, _ = Ingestor(today=datetime(2026, 11, 23).date()).ingest(
        "p1",
        [
            {"text": "去年今天在西湖", "day": "2025-11-23", "place": "西湖", "evidence_id": "ev1"},
            {"text": "去年今天也去了西湖", "day": "2025-11-23", "place": "西湖", "evidence_id": "ev2"},
        ],
    )
    candidates = EchoPolicy().candidates("p1", episodes, datetime(2026, 11, 23).date())
    assert candidates and candidates[0].why_now
    assert store.entries("p1") == []  # echo never writes


def test_llm_down_chain_still_returns_and_is_partial(tmp_path):
    _, orchestrator = _wired(tmp_path, llm_fail=True)
    result = orchestrator.run(TurnRequest(person_id="p1", text="今天有点累"))
    assert result.status is ResultStatus.PARTIAL and result.degraded
    assert result.reply
