"""Contracts: the frozen vocabulary. If this file fails, every module is suspect."""

from datetime import date

import pytest
from pydantic import ValidationError

from agents.provenance import Provenance
from harness.contracts import (
    MAX_ALTERNATES,
    Claim,
    ClaimBundle,
    Digest,
    DigestSection,
    MemoryLayer,
    MemoryProposal,
    OpKind,
    SearchQuery,
    TurnRequest,
    VerdictStatus,
)


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        TurnRequest(person_id="p1", text="hi", unexpected=1)


def test_contracts_are_frozen():
    request = TurnRequest(person_id="p1", text="hi")
    with pytest.raises(ValidationError):
        request.text = "changed"  # type: ignore[misc]


def test_blank_text_rejected():
    with pytest.raises(ValidationError):
        TurnRequest(person_id="p1", text="   ")


def test_alternates_limit_is_enforced():
    with pytest.raises(ValidationError):
        SearchQuery(query="q", person_id="p1", alternates=tuple(f"a{i}" for i in range(MAX_ALTERNATES + 1)))


def test_alternate_length_and_blank():
    with pytest.raises(ValidationError):
        SearchQuery(query="q", person_id="p1", alternates=("x" * 201,))
    with pytest.raises(ValidationError):
        SearchQuery(query="q", person_id="p1", alternates=("  ",))


def test_date_range_order():
    with pytest.raises(ValidationError):
        SearchQuery(query="q", person_id="p1", date_from=date(2026, 5, 1), date_to=date(2026, 4, 1))


def test_digest_chars_counts_sections_only():
    digest = Digest(person_id="p1", sections=(DigestSection(name="身份", text="abc"),))
    assert digest.chars == 3


def test_proposal_requires_text():
    with pytest.raises(ValidationError):
        MemoryProposal(op=OpKind.ADD, layer=MemoryLayer.FACT, text="")


def test_verdict_status_values_are_stable():
    assert {v.value for v in VerdictStatus} == {"pass", "needs_evidence", "mask", "reject", "handoff"}


def test_provenance_accepts_plain_claim_without_citation():
    verdict = Provenance().verify(ClaimBundle(claims=(Claim(text="今天过得怎么样？"),)), ())
    assert verdict.status is VerdictStatus.PASS
