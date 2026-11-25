"""B7-02 judge probes: taboo list, fail-open/fail-closed asymmetry."""

from __future__ import annotations

from app.domain.crisis.scan import scan, t2_reply, t3_reply
from app.domain.guard.empathy import GLOBAL_TABOO, check_empathy


def test_taboo_phrases_are_flagged():
    for phrase in ("别想太多", "你就是难过", "你其实很在意"):
        verdict = check_empathy("我很难过", f"没事，{phrase}。")
        assert verdict.status == "needs_rewrite"
        assert verdict.violations


def test_normal_chat_without_violation_passes():
    verdict = check_empathy("我很难过", "听起来今天真的很沉。你想先说哪一段？")
    assert verdict.status == "pass"


def test_private_without_citation_fails_closed():
    verdict = check_empathy("他搬走了", "别想太多。", private_without_citation=True)
    assert verdict.status == "reject"


def test_checker_error_fails_open_normally_but_closed_for_private():
    import app.domain.guard.empathy as module

    original = module._scan
    module._scan = lambda *_a, **_k: 1 / 0  # type: ignore[assignment]
    try:
        normal = check_empathy("今天有点累", "先歇一会儿。")
        private = check_empathy("他搬走了", "我听到了。", private_without_citation=True)
    finally:
        module._scan = original  # type: ignore[assignment]
    assert normal.status == "pass" and normal.fail_open is True
    assert private.status == "reject"


def test_taboo_is_a_single_declared_tuple():
    assert GLOBAL_TABOO == (
        "别想太多",
        "你为什么这样",
        "怀念吗",
        "你应该",
        "这有什么好难过",
        "你就是难过",
        "你其实很在意",
        "你一定是",
    )


def test_crisis_t3_has_hotline_and_identity_without_contact_claim():
    signal = scan("我不想活了")
    assert signal.level == "t3"
    reply = t3_reply()
    assert "12356" in reply and "AI 助手" in reply
    assert "已联系" not in reply and "已通知" not in reply


def test_crisis_t2_has_fixed_script_and_resources():
    signal = scan("我撑不住了")
    assert signal.level == "t2"
    assert "12356" in t2_reply()
