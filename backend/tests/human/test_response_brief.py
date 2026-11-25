"""B7 response-brief probes: prose, not mood labels; safety edges first."""

from __future__ import annotations

from app.domain.human import ResponseBrief, build_response_brief


def test_task_gets_practical_brief_without_empathy_preamble():
    brief = build_response_brief("帮我把这段总结一下，列成三条。")
    assert brief.brief_id == "task"
    assert "直接做事" in brief.how_she_begins
    assert "不要先铺一段" in brief.what_she_avoids
    assert "对方这一轮的原话是" in brief.what_she_heard


def test_emotional_event_puts_acknowledgement_before_advice():
    brief = build_response_brief("今天被领导骂了，烦死了。")
    assert brief.brief_id == "anger"
    assert "先承认" in brief.how_she_begins
    assert "不建议" in brief.what_she_avoids
    assert "先骂两句" in brief.how_she_begins


def test_grief_never_repairs_or_explains_meaning():
    brief = build_response_brief("我奶奶走了。")
    assert brief.brief_id == "grief"
    assert "不说“想开点”" in brief.what_she_avoids
    assert "不解释" in brief.what_she_avoids
    assert "允许" in brief.what_they_need


def test_health_never_diagnoses_and_points_to_professional_help():
    brief = build_response_brief("我是不是抑郁症？要不要吃药？")
    assert brief.brief_id == "health"
    assert "不能诊断" in brief.how_she_begins
    assert "不下诊断" in brief.what_she_avoids
    assert "专业帮助" in brief.what_she_avoids


def test_good_news_celebrates_and_asks_one_detail():
    brief = build_response_brief("我升职了，终于等到这一天。")
    assert brief.brief_id == "good_news"
    assert "真的高兴" in brief.how_she_begins
    assert "不泼冷水" in brief.what_she_avoids
    assert "只追问一个" in brief.how_much_she_says


def test_self_blame_separates_fact_from_identity():
    brief = build_response_brief("我是不是特别没用，什么都做不好。")
    assert brief.brief_id == "self_blame"
    assert "我先不接“没用”这个词" in brief.how_she_begins
    assert "不替对方下人格结论" in brief.what_she_avoids


def test_ambiguous_admits_it_does_not_know():
    brief = build_response_brief("不知道为啥难受。")
    assert brief.brief_id == "ambiguous"
    assert "判断不了" in brief.how_sure_she_is
    assert "不硬贴情绪标签" in brief.what_she_avoids
    assert "只问一句" in brief.how_much_she_says


def test_low_mood_does_not_force_a_lift():
    brief = build_response_brief("最近很累，什么都不想动。")
    assert brief.brief_id == "low_mood"
    assert "不灌鸡汤" in brief.what_she_avoids
    assert "陪着" in brief.how_much_she_says


def test_anxiety_separates_controllable_from_uncontrollable():
    brief = build_response_brief("我担心万一面试不过怎么办？")
    assert brief.brief_id == "anxiety"
    assert "可控和不可控" in brief.what_they_need
    assert "肯定没事" in brief.what_she_avoids


def test_decision_does_not_choose_for_the_user():
    brief = build_response_brief("我不知道要不要分手。")
    assert brief.brief_id == "decision"
    assert "不替对方做决定" in brief.what_she_avoids
    assert "最怕选错" in brief.how_she_begins


def test_every_brief_is_full_prose_and_has_all_sections():
    briefs = [
        build_response_brief(text)
        for text in (
            "帮我查一下这个。",
            "我今天很累。",
            "我奶奶走了。",
            "我升职了。",
            "我是不是抑郁症？",
            "不知道为啥难受。",
        )
    ]
    for brief in briefs:
        assert isinstance(brief, ResponseBrief)
        assert len(brief.what_she_sees) >= 30
        assert len(brief.what_they_need) >= 20
        assert len(brief.how_she_begins) >= 20
        assert len(brief.what_she_avoids) >= 20
        assert len(brief.how_much_she_says) >= 15
        assert len(brief.what_she_heard) >= 20
        assert len(brief.how_sure_she_is) >= 10
        prompt = brief.as_prompt()
        assert "brief_id=" not in prompt
        assert "anger" not in prompt
        assert "grief" not in prompt
