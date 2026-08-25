"""敏感词规则引擎测试（B5b · 2026-08-20 两档升级）

覆盖：
  - 词表类 reject：政治/色情/涉枪爆整条拦截
  - 广告类 mask：打码保留
  - 号码类 mask：身份证/手机/银行卡打码（保留头尾）
  - 归一化：全角/空格变体仍命中
  - 正常文本 pass
  - 事件级敏感分类（B5b Wave1）：7 类软敏感 + 违规词热加入 + 搜索规则级过滤
"""


from app.services.external.sensitive_words import (
    add_violation_word,
    check_event_sensitive,
    check_sensitive,
    filter_sensitive_rule,
)


class TestWordReject:
    def test_political_reject(self):
        r = check_sensitive("支持法轮功的言论")
        assert r["pass"] is False
        assert r["action"] == "reject"

    def test_porn_reject(self):
        r = check_sensitive("约裸聊加微信")
        assert r["pass"] is False
        assert r["action"] == "reject"

    def test_weapon_reject(self):
        r = check_sensitive("自制炸药的方法")
        assert r["pass"] is False
        assert r["action"] == "reject"

    def test_normalize_variant_still_hits(self):
        # 空格/全角变体 → 归一化后命中
        r = check_sensitive("法 轮 功")
        assert r["pass"] is False
        r2 = check_sensitive("支持法轮功")  # 全角
        assert r2["pass"] is False


class TestAdMask:
    def test_ad_word_masked(self):
        r = check_sensitive("专业代开发票联系电话")
        assert r["pass"] is True
        assert r["action"] == "mask"
        assert "*" in r["masked_text"]
        assert "代开发票" not in r["masked_text"]

    def test_ad_word_variant_masked_in_original(self):
        """审查修复(P1-13)：空格变体命中（normalized）→ 原文必须实际打码

        原实现 matched 来自归一化文本、却用原文 replace——"代 开发 票"
        归一化命中但原文 replace 落空，广告词漏打码。
        """
        r = check_sensitive("加微信 代 开发 票 优惠")
        assert r["pass"] is True
        assert r["action"] == "mask"
        # 原文中的广告词各字符必须被打码（不再原样残留）
        assert "代" not in r["masked_text"]
        assert "票" not in r["masked_text"]
        assert "*" in r["masked_text"]


class TestNumberMask:
    def test_phone_masked(self):
        r = check_sensitive("我的手机号是13812345678")
        assert r["pass"] is True
        assert r["action"] == "mask"
        assert "138****5678" in r["masked_text"]

    def test_id_card_masked(self):
        r = check_sensitive("身份证号11010119900307789X")
        assert r["pass"] is True
        assert r["action"] == "mask"
        assert "110***********789X" in r["masked_text"]  # 保留前3后4

    def test_bank_card_masked(self):
        r = check_sensitive("卡号6222021234567890123")
        assert r["pass"] is True
        assert r["action"] == "mask"
        assert "6222***********0123" in r["masked_text"]

    def test_number_in_normal_text(self):
        # 长数字（如数量）不打码——16-19 位才算卡号
        r = check_sensitive("这个项目预算500万")
        assert r["pass"] is True
        assert r["action"] == "pass"


class TestUrlBlacklist:
    """网址黑名单：域名提取 → 集合查询 → 打码（2026-08-20 接入）"""

    def test_blacklisted_domain_masked(self):
        r = check_sensitive("注册这个 0008-qq.cn 网站有优惠")
        assert r["pass"] is True
        assert r["action"] == "mask"
        assert "0008-qq.cn" not in r["masked_text"]

    def test_url_with_protocol_masked(self):
        r = check_sensitive("详情见 https://www.000wyt.com 页面")
        assert r["action"] == "mask"
        assert "000wyt.com" not in r["masked_text"]

    def test_normal_domain_not_masked(self):
        r = check_sensitive("可以访问 github.com 学习")
        assert r["action"] == "pass"
        assert "github.com" in r["masked_text"]

    def test_sensitive_word_plus_url_rejects(self):
        """审查 CRITICAL 修复：敏感词 + 黑名单网址同现 → reject 恒优先（URL 分支不得旁路词表）"""
        r = check_sensitive("支持法轮功 详情见 https://www.000wyt.com")
        assert r["pass"] is False
        assert r["action"] == "reject"
        assert "政治类" in r["categories"]


class TestPass:
    def test_normal_text(self):
        r = check_sensitive("今天去苏州吃了松鼠桂鱼，很开心")
        assert r["pass"] is True
        assert r["action"] == "pass"
        assert r["masked_text"] == "今天去苏州吃了松鼠桂鱼，很开心"


class TestEventSensitive:
    """事件级敏感分类（B5b Wave1：规则层，软敏感，独立于硬规则 reject/mask）"""

    def test_breakup_hit(self):
        r = check_event_sensitive("去年我们分手了，后来再也没联系")
        assert r["pass"] is False
        assert "分手" in r["categories"]
        assert r["matched"]

    def test_death_hit(self):
        r = check_event_sensitive("爷爷去年去世了，全家都很伤心")
        assert "离世" in r["categories"]

    def test_health_hit(self):
        r = check_event_sensitive("妈妈确诊癌症，下个月住院手术")
        assert "健康" in r["categories"]

    def test_money_hit(self):
        r = check_event_sensitive("那年生意破产欠了不少债")
        assert "金钱" in r["categories"]

    def test_family_conflict_hit(self):
        r = check_event_sensitive("那阵子家里天天吵架，婆媳关系很僵")
        assert "家庭矛盾" in r["categories"]

    def test_normal_text_pass(self):
        r = check_event_sensitive("今天去苏州吃了松鼠桂鱼，很开心")
        assert r["pass"] is True
        assert r["categories"] == []

    def test_normalized_variant_hits(self):
        # 全角/空格变体归一化后仍命中（与硬规则引擎同一归一化）
        r = check_event_sensitive("分 手 之后我再没提过")
        assert "分手" in r["categories"]

    def test_hard_rule_independent(self):
        """事件级与硬规则互不干扰：正常文本两入口都 pass；违规文本硬规则 reject"""
        assert check_sensitive("昨天我们分手了")["action"] == "pass"
        assert check_event_sensitive("支持法轮功的言论")["pass"] is True

    def test_violation_word_hot_add(self):
        """违规词热加入（回流）：进程内立即进入事件级判定，归入"回流词"类别"""
        assert check_event_sensitive("我们彻底绝交了")["pass"] is True
        add_violation_word("绝交")
        try:
            r = check_event_sensitive("我们彻底绝交了")
            assert r["pass"] is False
            assert "回流词" in r["categories"]
        finally:
            # 清理进程内热加入，避免影响其他用例
            from app.services.external.sensitive_words import _EVENT_REFLUX_WORDS

            _EVENT_REFLUX_WORDS.discard("绝交")

    def test_violation_word_hot_add_with_category(self):
        """带类别热加入：并入对应事件类别（LLM 回流场景）"""
        add_violation_word("闹崩", "分手")
        try:
            r = check_event_sensitive("我们彻底闹崩了")
            assert "分手" in r["categories"]
        finally:
            from app.services.external.sensitive_words import _load_event_words

            _load_event_words()["分手"].discard("闹崩")

    def test_empty_text(self):
        r = check_event_sensitive("")
        assert r["pass"] is True


class TestSearchRuleFilter:
    """搜索/摘要规则级敏感过滤（B5b-1 🟢 规则级，不过模型；供 Agent A 接线 rag.py）"""

    def test_hard_sensitive_rejected(self):
        assert filter_sensitive_rule("支持法轮功的言论") is True

    def test_normal_text_allowed(self):
        assert filter_sensitive_rule("今天去苏州吃了松鼠桂鱼") is False

    def test_event_sensitive_not_blocked_for_search(self):
        """事件级软敏感不阻断搜索（规则级过滤只挡硬违规）"""
        assert filter_sensitive_rule("去年我们分手了") is False
