"""Policy: crisis handoff, forbidden phrasing, boundary detection, sentence split."""

from agentv3.contracts import GuardAction, GuardRequest, Intent
from agentv3.memory.policy import Policy


def _req(text: str) -> GuardRequest:
    return GuardRequest(person_id="p1", text=text, intent=Intent.EMOTION)


def test_crisis_hands_off_without_claiming_contact():
    verdict = Policy().screen_input(_req("我不想活了"))
    assert verdict.action is GuardAction.HANDOFF
    assert "已联络" not in (verdict.reply or "")
    assert "急救" in (verdict.reply or "")


def test_normal_text_is_allowed():
    assert Policy().screen_input(_req("今天有点累")).action is GuardAction.ALLOW


def test_forbidden_output_phrase_is_rejected():
    verdict = Policy().check_output("我已经通知了紧急联系人")
    assert verdict.action is GuardAction.REJECT


def test_boundary_sentence_is_masked_by_provenance():
    assert Policy().boundary_hit("去年葬礼那天") == "葬礼"


def test_sentences_split_on_chinese_punctuation():
    assert Policy.sentences("一。二！三？") == ["一", "二", "三"]
