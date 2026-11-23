"""Provenance: rules decide first, and a mask can never be silently lifted."""

from agentv3.agents.provenance import Provenance
from agentv3.contracts import Claim, ClaimBundle, VerdictStatus
from agentv3.memory.policy import Policy


def _verify(claims, evidence=(), corrections=(), crisis=False):
    return Provenance(Policy(), corrections).verify(ClaimBundle(claims=claims, crisis=crisis), evidence)


def test_private_claim_without_citation_needs_evidence():
    verdict = _verify([Claim(text="她去年住在城西", kind="private")])
    assert verdict.status is VerdictStatus.NEEDS_EVIDENCE


def test_private_claim_with_held_citation_passes():
    verdict = _verify([Claim(text="她去年住在城西 [S1]", cites=("S1",), kind="private")], evidence=("S1",))
    assert verdict.status is VerdictStatus.PASS
    assert verdict.claims[0].evidence_ids == ("S1",)


def test_dangling_citation_is_needs_evidence():
    verdict = _verify([Claim(text="她喜欢桂花 [S3]", cites=("S3",), kind="private")], evidence=("S1",))
    assert verdict.status is VerdictStatus.NEEDS_EVIDENCE
    assert verdict.claims[0].reason == "dangling_cite"


def test_boundary_word_masks_sentence_and_keeps_the_rest():
    verdict = _verify([Claim(text="去年葬礼那天她很难过"), Claim(text="今天天气不错")])
    assert verdict.status is VerdictStatus.MASK
    assert verdict.fixed_reply == "今天天气不错"


def test_contradicting_a_correction_is_rejected():
    verdict = _verify([Claim(text="她喜欢桂花")], corrections=("她不喜欢桂花",))
    assert verdict.status is VerdictStatus.REJECT


def test_correction_wins_over_model_even_with_citation():
    verdict = _verify([Claim(text="她不喜欢桂花", cites=("S1",))], evidence=("S1",), corrections=("她不喜欢桂花",))
    assert verdict.status is VerdictStatus.REJECT


def test_crisis_flag_short_circuits_to_handoff():
    assert _verify([], crisis=True).status is VerdictStatus.HANDOFF


def test_plain_greeting_is_not_forced_to_cite():
    assert _verify([Claim(text="我在听")]).status is VerdictStatus.PASS
