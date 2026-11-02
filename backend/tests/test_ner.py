"""查询侧 NER 实体抽取测试（B2-2 · 2026-08-19）

覆盖：地名规则（城市/省份/地标/后缀形态）、人物规则（小X/称谓）、
      普通名词不误判、LLM 兜底（mock JSON 解析/失败回落）。
"""


import pytest
from app.services.ner import _extract_llm, _extract_person_rules, _extract_place_rules, extract_entities

# H3/R8#10：纯规则/LLM 打桩单测 → unit 分层（-m unit 秒级快速回归）
pytestmark = pytest.mark.unit

# R8#5（2026-08-27）：9 个单断言规则用例压缩为表驱动参数化，
# 中文场景语义保留在 parametrize id。
PLACE_RULE_CASES = [
    ("去年在苏州吃的松鼠桂鱼", "苏州"),
    ("回广东过年", "广东"),
    ("河北省的旅行", "河北"),
    ("江苏苏州的园林", "苏州"),   # 城市优先于省份（最长优先 + 排序）
    ("去景德镇出差", "景德镇"),   # 未入表地名走后缀形态
    ("在拉萨市拍的", "拉萨"),     # 市后缀剥除
    ("外滩的夜景", "外滩"),       # 地标
    ("公园散步", None),           # 普通名词不误判为地点（防过过滤）
    ("在学校上课", None),
]


@pytest.mark.parametrize(
    ("text", "expected"),
    PLACE_RULE_CASES,
    ids=[
        "城市抽取-苏州",
        "省份抽取-广东",
        "省份简称-河北",
        "城市优先于省份",
        "后缀形态-景德镇",
        "市后缀剥除-拉萨",
        "地标识别-外滩",
        "普通名词不误判-公园",
        "普通名词不误判-学校",
    ],
)
def test_extract_place_rules(text, expected):
    """地名规则（R8#5 参数化）：城市/省份/后缀/地标抽取 + 普通名词不误判"""
    assert _extract_place_rules(text) == expected


PERSON_RULE_CASES = [
    ("和小张一起吃的饭", "小张"),
    ("王老师讲的话", "王老师"),
    ("考研数学真题", None),
]


@pytest.mark.parametrize(
    ("text", "expected"),
    PERSON_RULE_CASES,
    ids=["小姓称谓-小张", "尊称识别-王老师", "普通词不误判-考研数学"],
)
def test_extract_person_rules(text, expected):
    """人物规则（R8#5 参数化）：小X/称谓抽取 + 不误判"""
    assert _extract_person_rules(text) == expected


class TestExtractEntities:
    def test_place_and_person(self):
        r = extract_entities("和小张在苏州吃的")
        assert r["place"] == "苏州"
        assert r["person"] == "小张"

    def test_llm_fallback_used_when_rules_miss(self, monkeypatch):
        # 规则未命中 + enable_llm=True → LLM 抽取
        import app.services.ner as ner_mod

        monkeypatch.setattr(ner_mod, "_extract_llm", lambda q: {"place": "景德镇", "person": ""})
        r = extract_entities("上次去那儿买的瓷器", enable_llm=True)
        assert r["place"] == "景德镇"

    def test_llm_fallback_never_overrides_rules(self, monkeypatch):
        import app.services.ner as ner_mod

        monkeypatch.setattr(ner_mod, "_extract_llm", lambda q: {"place": "北京", "person": ""})
        r = extract_entities("苏州的园林", enable_llm=True)
        assert r["place"] == "苏州"  # 规则结果优先

    def test_llm_failure_falls_back_to_rules(self, monkeypatch):
        import app.services.ner as ner_mod

        def boom(q):
            raise RuntimeError("LLM 不可用")

        monkeypatch.setattr(ner_mod, "_extract_llm", boom)
        r = extract_entities("在杭州拍的", enable_llm=True)
        assert r["place"] == "杭州"


class TestLLMExtract:
    def test_json_parse(self, monkeypatch):
        # TD-P2B（S1-L7）收口：ner 已统一走 llm_ops.base.chat_text（此前直接
        # import dashscope._chat_text）→ 打桩目标同步到 base.chat_text
        import app.services.llm_ops.base as base_mod

        monkeypatch.setattr(
            base_mod,
            "chat_text",
            lambda system, user: '{"place": "苏州", "person": "小张"}',
        )
        assert _extract_llm("任意") == {"place": "苏州", "person": "小张"}

    def test_bad_json_returns_empty(self, monkeypatch):
        import app.services.llm_ops.base as base_mod

        monkeypatch.setattr(base_mod, "chat_text", lambda system, user: "不是JSON")
        assert _extract_llm("任意") == {}
