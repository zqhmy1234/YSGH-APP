"""Profile digest: 8 sections, <=600 chars, three honest states."""

from __future__ import annotations

from app.services.profile_digest import DIGEST_MAX_CHARS, SECTIONS, build_agent_digest


def test_missing_dimensions_is_unavailable_with_no_fabrication():
    digest = build_agent_digest(None)
    assert digest.status == "unavailable"
    assert "不要编造" in digest.text


def test_empty_dimensions_is_insufficient_with_explanation():
    digest = build_agent_digest({})
    assert digest.status == "insufficient_data"
    assert "不要假装了解" in digest.text


def test_unknown_dimensions_do_not_count_as_data():
    digest = build_agent_digest({"no_such_dimension": "x"})
    assert digest.status == "insufficient_data"


def test_ok_projection_uses_fixed_order_and_budget():
    digest = build_agent_digest(
        {
            "identity": "退休教师",
            "relation_core": ["女儿", "外孙"],
            "habits": "早起",
            "preference": "桂花",
            "life_events": "1988 年搬到杭州",
            "taboo": "不提葬礼",
            "recent_state": "最近睡眠浅",
            "pending": "想整理老照片",
        }
    )
    assert digest.status == "ok"
    assert digest.chars <= DIGEST_MAX_CHARS
    assert [s.name for s in digest.sections] == list(SECTIONS)
    assert "不要编造" not in digest.text


def test_never_exceeds_budget_with_long_values():
    dimensions = {key: "很长的值" * 100 for key in ("identity", "habits", "preference", "life_events")}
    digest = build_agent_digest(dimensions)
    assert digest.status == "ok"
    assert digest.chars <= DIGEST_MAX_CHARS
