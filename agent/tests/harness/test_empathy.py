"""EmpathyChecker: taboo single source, fail-open vs fail-closed, negative probes."""

from __future__ import annotations

from harness.empathy import GLOBAL_TABOO, directive_for, empathy_violations
from prompts.role_card import RoleCardRepository


def test_global_taboo_is_found_in_every_card():
    repo = RoleCardRepository()
    for role_id in repo.available():
        card = repo.load(role_id)
        extra = set(card.taboo) - set(GLOBAL_TABOO)
        assert not extra, (role_id, extra)


def test_judging_reply_needs_rewrite_with_quote():
    verdict = empathy_violations("我有点累", "别想太多，你就是难过。")
    assert verdict.status == "needs_rewrite"
    assert any(v.code.startswith("taboo:") for v in verdict.violations)
    assert any(v.quote for v in verdict.violations)


def test_normal_chat_is_fail_open_when_checker_has_nothing():
    assert empathy_violations("今天有点累", "听起来今天挺耗人的。").status == "pass"


def test_private_without_citation_fail_closed():
    verdict = empathy_violations(
        "她住哪",
        "别想太多。",
        private_without_citation=True,
    )
    assert verdict.status == "reject"


def test_crisis_and_minor_fail_closed():
    assert empathy_violations("x", "你就是难过", crisis=True).status == "reject"
    assert empathy_violations("x", "你就是难过", minor=True).status == "reject"


def test_directive_is_short_and_contains_five_dims():
    text = directive_for(warmth=4, verbosity=2, advice=1, tone="gentle", emotion_display="off")
    assert len(text) <= 100
    assert "4/5" in text and "gentle" in text and "off" in text
