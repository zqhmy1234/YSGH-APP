"""查询侧 NER 实体抽取测试（B2-2 · 2026-08-19）

覆盖：地名规则（城市/省份/地标/后缀形态）、人物规则（小X/称谓）、
      普通名词不误判、LLM 兜底（mock JSON 解析/失败回落）。
"""


from app.services.ner import _extract_llm, _extract_person_rules, _extract_place_rules, extract_entities


class TestPlaceRules:
    def test_city_extraction(self):
        assert _extract_place_rules("去年在苏州吃的松鼠桂鱼") == "苏州"

    def test_province_extraction(self):
        assert _extract_place_rules("回广东过年") == "广东"
        assert _extract_place_rules("河北省的旅行") == "河北"

    def test_city_beats_province(self):
        # "江苏" 和 "苏州" 同时出现 → 城市优先（最长优先 + 排序）
        assert _extract_place_rules("江苏苏州的园林") == "苏州"

    def test_suffix_form(self):
        # 未入表地名走后缀形态
        assert _extract_place_rules("去景德镇出差") == "景德镇"
        assert _extract_place_rules("在拉萨市拍的") == "拉萨"

    def test_landmark(self):
        assert _extract_place_rules("外滩的夜景") == "外滩"

    def test_common_noun_not_place(self):
        # 普通名词不误判为地点（防过过滤）
        assert _extract_place_rules("公园散步") is None
        assert _extract_place_rules("在学校上课") is None


class TestPersonRules:
    def test_xiao_surname(self):
        assert _extract_person_rules("和小张一起吃的饭") == "小张"

    def test_honorific(self):
        assert _extract_person_rules("王老师讲的话") == "王老师"

    def test_no_person(self):
        assert _extract_person_rules("考研数学真题") is None


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
