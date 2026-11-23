"""Provenance: rules first, model second (D3).

Rules answer four questions without a model:
  * does a private fact come with a citation from this turn's evidence?
  * does the citation exist in this turn's evidence set at all?
  * does the sentence contradict a human correction?
  * does the sentence touch a boundary word and must be masked?

The model is only consulted for the residual that the rules mark uncertain, and
its verdict can never upgrade ``mask``/``reject`` (no silent un-masking).
"""

from __future__ import annotations

from typing import Sequence

from ..contracts import Claim, ClaimBundle, ClaimVerdict, Verdict, VerdictStatus
from ..memory.policy import Policy

_EVIDENCE_KINDS = ("private", "numeric", "date", "name")
_NEGATIONS = ("不是", "并没有", "从来没", "已不在")


class Provenance:
    def __init__(self, policy: Policy | None = None, corrections: Sequence[str] = ()) -> None:
        self._policy = policy or Policy()
        self._corrections = tuple(c for c in corrections if c.strip())

    def verify(self, bundle: ClaimBundle, evidence_ids: Sequence[str]) -> Verdict:
        if bundle.crisis:
            return Verdict(status=VerdictStatus.HANDOFF, reason="crisis_marker")

        held = set(evidence_ids) | set(bundle.evidence)
        verdicts: list[ClaimVerdict] = []
        masked: list[str] = []
        worst = VerdictStatus.PASS

        for claim in bundle.claims:
            v = self._check_one(claim, held)
            verdicts.append(v)
            if v.reason.startswith("boundary:"):
                masked.append(claim.text)
            worst = _worse(worst, _status_of(v))

        fixed = "".join(c.text for c in bundle.claims if c.text not in masked) if masked else None
        return Verdict(
            status=worst,
            claims=tuple(verdicts),
            reason="" if worst is VerdictStatus.PASS else worst.value,
            fixed_reply=fixed,
        )

    def _check_one(self, claim: Claim, held: set[str]) -> ClaimVerdict:
        if not claim.text.strip():
            return ClaimVerdict(text=claim.text, grounded=False, reason="empty_claim")

        for correction in self._corrections:
            core = _strip_negation(correction)
            if core and core in _strip_negation(claim.text):
                if _has_negation(correction) != _has_negation(claim.text):
                    return ClaimVerdict(text=claim.text, grounded=False, reason="contradicts_correction", evidence_ids=())
                if correction in claim.text and not any(neg in claim.text for neg in _NEGATIONS):
                    if correction not in claim.cites:  # a corrected fact needs its correction citation
                        return ClaimVerdict(text=claim.text, grounded=False, reason="contradicts_correction", evidence_ids=())

        boundary = self._policy.boundary_hit(claim.text)
        if boundary:
            return ClaimVerdict(text=claim.text, grounded=False, reason=f"boundary:{boundary}")

        unknown = [c for c in claim.cites if c not in held]
        if unknown:
            return ClaimVerdict(text=claim.text, grounded=False, reason="dangling_cite", evidence_ids=tuple(c for c in claim.cites if c in held))

        if claim.kind in _EVIDENCE_KINDS and not claim.cites:
            return ClaimVerdict(text=claim.text, grounded=False, reason="missing_citation")

        return ClaimVerdict(text=claim.text, grounded=True, evidence_ids=tuple(c for c in claim.cites if c in held), reason="ok")


def _status_of(v: ClaimVerdict) -> VerdictStatus:
    if v.grounded:
        return VerdictStatus.PASS
    if v.reason.startswith("boundary:"):
        return VerdictStatus.MASK
    if v.reason == "contradicts_correction":
        return VerdictStatus.REJECT
    return VerdictStatus.NEEDS_EVIDENCE


_NEG_CHARS = "不没别未"


def _strip_negation(text: str) -> str:
    return "".join(ch for ch in text if ch not in _NEG_CHARS and ch not in "，。！？ ,.!?").strip()


def _has_negation(text: str) -> bool:
    return any(ch in text for ch in _NEG_CHARS)


_ORDER = {
    VerdictStatus.PASS: 0,
    VerdictStatus.NEEDS_EVIDENCE: 1,
    VerdictStatus.MASK: 2,
    VerdictStatus.REJECT: 3,
    VerdictStatus.HANDOFF: 4,
}


def _worse(a: VerdictStatus, b: VerdictStatus) -> VerdictStatus:
    return b if _ORDER[b] > _ORDER[a] else a
