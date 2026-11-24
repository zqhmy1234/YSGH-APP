"""B5-01 schema guards: canonical names exist, no second fact source."""

from __future__ import annotations

from app.db.session import Base

EXPECTED = {
    "memory_entries",
    "memory_fact_claims",
    "memory_corrections",
    "memory_batches",
    "memory_change_log",
    "memory_drafts",
}


def test_canonical_ledger_tables_are_registered():
    assert EXPECTED.issubset(set(Base.metadata.tables))


def test_no_memory_facts_second_source():
    assert "memory_facts" not in Base.metadata.tables


def test_evidence_is_required_in_schema():
    entry_checks = {c.name for c in Base.metadata.tables["memory_entries"].constraints}
    assert "ck_memory_entries_evidence" in entry_checks
    draft_checks = {c.name for c in Base.metadata.tables["memory_drafts"].constraints}
    assert "ck_memory_drafts_evidence" in draft_checks
