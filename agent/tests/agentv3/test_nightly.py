"""Nightly batch: deterministic hash, idempotent retry, empty night writes nothing."""

from datetime import date

from harness.contracts import MemoryLayer
from jobs.nightly import NightlyJob
from memory.ledger import MemoryStore


def _episode(text="她喜欢桂花"):
    return {"text": text, "layer": "fact", "evidence": ["e1"], "day": "2026-11-20"}


def test_batch_hash_is_deterministic_and_order_insensitive():
    day = date(2026, 11, 23)
    assert NightlyJob.batch_hash("p1", day, ["b", "a"]) == NightlyJob.batch_hash("p1", day, ["a", "b"])


def test_nightly_commits_and_retry_is_idempotent():
    store = MemoryStore()
    job = NightlyJob(store)
    first = job.run("p1", [_episode()], day=date(2026, 11, 23))
    second = job.run("p1", [_episode()], day=date(2026, 11, 23))
    assert first.committed and second.idempotent
    assert len(store.entries("p1")) == 1


def test_empty_night_writes_nothing():
    store = MemoryStore()
    receipt = NightlyJob(store).run("p1", [], day=date(2026, 11, 23))
    assert receipt.committed == () and store.entries("p1") == []


def test_unknown_evidence_is_rejected_by_scope():
    store = MemoryStore()
    receipt = NightlyJob(store).run("p1", [_episode()], day=date(2026, 11, 23), allowed_evidence={"e2"})
    assert receipt.rolled_back and store.entries("p1") == []


def test_fact_episode_without_evidence_is_rejected():
    store = MemoryStore()
    receipt = NightlyJob(store).run(
        "p1", [{"text": "她喜欢桂花", "layer": "fact", "evidence": []}], day=date(2026, 11, 23)
    )
    assert receipt.rolled_back


def test_narrative_episode_without_evidence_is_allowed():
    store = MemoryStore()
    receipt = NightlyJob(store).run("p1", [{"text": "今天聊了很久", "layer": "narrative"}], day=date(2026, 11, 23))
    assert receipt.committed
    assert store.entries("p1")[0].layer is MemoryLayer.NARRATIVE
