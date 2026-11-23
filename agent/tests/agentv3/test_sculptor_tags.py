from __future__ import annotations

from datetime import date

from agentv3.agents.sculptor import Sculptor
from agentv3.contracts import Episode, MemoryLayer, MemoryProposal, OpKind, SourceKind, TagSet
from agentv3.domain.ingest import Ingestor
from agentv3.memory.ledger import MemoryStore


def test_ingestor_attaches_tags():
    episodes, receipt = Ingestor(today=date(2026, 11, 23)).ingest(
        "p1",
        [{"text": "今天去西湖，很开心", "source": "text", "origin": "personal", "place": "西湖"}],
    )
    assert receipt.accepted
    assert episodes[0].tags.library == "personal"
    assert episodes[0].tags.mood == "开心"


def test_sculptor_propagates_episode_tags():
    store = MemoryStore()
    sculpt = Sculptor(store)
    proposals = sculpt.propose_from_episodes(
        "p1",
        [
            {
                "text": "今天去西湖",
                "layer": "narrative",
                "evidence": ["e1"],
                "day": "2026-11-23",
                "tags": {"library": "personal", "category": "事件", "mood": "开心", "content_format": "text"},
            }
        ],
    )
    assert proposals[0].tags.library == "personal"
    assert proposals[0].tags.category == "事件"


def test_knowledge_tags_require_asset_layer():
    store = MemoryStore()
    sculpt = Sculptor(store)
    bad = (
        MemoryProposal(
            op=OpKind.ADD,
            layer=MemoryLayer.FACT,
            text="RAG 入门",
            evidence=("c1",),
            tags=TagSet(library="knowledge", content_type="文章"),
        ),
    )
    issues = sculpt.validate("p1", bad)
    assert any(code == "layer_disallowed" for _, code in issues)


def test_tags_survive_commit():
    store = MemoryStore()
    sculpt = Sculptor(store)
    episode = Episode(
        episode_id="e_1",
        person_id="p1",
        source=SourceKind.TEXT,
        text="今天去西湖",
        day=date(2026, 11, 23),
        evidence_id="raw1",
        tags=TagSet(library="personal", category="事件", mood="开心"),
    )
    proposal = MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.NARRATIVE, text=episode.text, evidence=("raw1",), tags=episode.tags)
    receipt = sculpt.apply(__import__("agentv3.contracts", fromlist=["ProposalBatch"]).ProposalBatch(person_id="p1", batch_hash="hash-0001", proposals=(proposal,)), allowed_evidence={"raw1"})
    assert receipt.committed
    entry = store.entries("p1")[0]
    assert entry.tags.mood == "开心"
