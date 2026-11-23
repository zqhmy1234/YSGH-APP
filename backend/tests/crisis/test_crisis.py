"""Crisis rules: T3 fixed reply, no contact claims, no model."""

from app.domain.crisis.scan import RESOURCE_LINES, scan, t3_reply


def test_t3_triggers_with_resources():
    signal = scan("我真的不想活了")
    assert signal.level == "t3"
    assert RESOURCE_LINES == signal.resources


def test_t2_and_t1():
    assert scan("我撑不住了").level == "t2"
    assert scan("最近很绝望").level == "t1"
    assert scan("今天天气不错").level == "none"


def test_t3_reply_does_not_claim_contact_or_diagnose():
    reply = t3_reply()
    assert "已联络" not in reply
    assert "已经通知" not in reply
    assert "12356" in reply
    assert "AI" in reply


def test_crisis_record_has_no_contact_field():
    from app.ports.crisis import CrisisRecord

    assert "contact" not in str(CrisisRecord.__annotations__).lower()
    assert "escalation_note" in CrisisRecord.__annotations__
