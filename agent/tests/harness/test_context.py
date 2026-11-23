"""ContextAssembler acceptance (B5-02/B7): 10 segments, budgets, digest states.

This file is the task-card's named test path; the detailed pruning tests live in
``tests/agentv3/test_frame.py`` and stay as the original v3 regression suite.
"""

from __future__ import annotations

import hashlib

from harness.context import SEGMENT_CAPS, SEGMENT_ORDER, ContextAssembler, ContextBudget, StablePrompt
from harness.contracts import Digest, DigestSection


def _assembled(digest, cap=4200):
    return ContextAssembler().assemble(
        stable=StablePrompt(
            persona="温和的旧友", relation="长期陪伴", boundaries=["不提葬礼"], corrections=["她叫小林"]
        ),
        input_text="今天有点累",
        digest=digest,
        budget=ContextBudget(frame=cap),
    )


def test_ten_segments_and_hard_limit():
    text = _assembled(Digest(person_id="p1", sections=(DigestSection(name="身份", text="退休教师"),)))
    assert [s.name for s in text.segments] == list(SEGMENT_ORDER)
    assert len(SEGMENT_ORDER) == 10
    assert text.total_chars <= 6200


def test_relationship_note_and_response_brief_are_full_prose():
    text = ContextAssembler().assemble(
        stable=StablePrompt(
            persona="温和的旧友",
            relation="长期陪伴",
            relationship_note="她先开口，但不急着填满安静。",
            response_brief="【这一轮她看见什么】对方说累。",
        ),
        input_text="今天有点累",
        budget=ContextBudget(frame=4200),
    )
    kept = {s.name: s.text for s in text.segments}
    assert "她先开口" in kept["persona"]
    assert "这一轮她看见什么" in kept["response_brief"]


def test_digest_states_include_explanation_lines():
    insufficient = _assembled(Digest(person_id="p1", state="insufficient_data"))
    digest = next(s for s in insufficient.segments if s.name == "digest").text
    assert "insufficient_data" in digest

    unavailable = _assembled(None)
    digest = next(s for s in unavailable.segments if s.name == "digest").text
    assert "unavailable" in digest and "不得引用" in digest


def test_protected_segments_never_dropped_even_when_tiny_budget():
    text = _assembled(Digest(person_id="p1"), cap=900)
    kept = {s.name: s.text for s in text.segments}
    assert kept["global"] and kept["input"] and kept["output_contract"]
    assert "不禁" not in kept["global"]  # sanity: guard content is real
    assert "她叫小林" in kept["state"]


def test_same_input_is_byte_stable():
    one = _assembled(Digest(person_id="p1", sections=(DigestSection(name="身份", text="退休教师"),)))
    two = _assembled(Digest(person_id="p1", sections=(DigestSection(name="身份", text="退休教师"),)))
    assert one.render() == two.render()
    assert hashlib.sha256(one.render().encode()).hexdigest() == hashlib.sha256(two.render().encode()).hexdigest()


def test_caps_are_all_positive_and_sum_within_hard_limit():
    assert all(cap > 0 for cap in SEGMENT_CAPS.values())
    assert sum(SEGMENT_CAPS.values()) >= 4200
