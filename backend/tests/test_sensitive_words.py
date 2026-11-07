"""敏感词规则引擎测试（B5b · 2026-08-20 两档升级）

覆盖：
  - 词表类 reject：政治/色情/涉枪爆整条拦截
  - 广告类 mask：打码保留
  - 号码类 mask：身份证/手机/银行卡打码（保留头尾）
  - 归一化：全角/空格变体仍命中
  - 正常文本 pass
  - 事件级敏感分类（B5b Wave1）：7 类软敏感 + 违规词热加入 + 搜索规则级过滤

H3/R8#5（2026-08-27）：同构单断言用例表驱动参数化（中文场景语义保留在 parametrize id）。
"""


import pytest
from app.services.external.sensitive_words import (
    add_violation_word,
    check_event_sensitive,
    check_sensitive,
    filter_sensitive_rule,
)

# H3/R8#10：纯规则引擎单测（无外部依赖）→ unit 分层
pytestmark = pytest.mark.unit


class TestWordReject:
    """词表类整条拦截（政治/色情/涉枪爆 → reject）"""

    REJECT_CASES = [
        ("支持法轮功的言论", "政治"),
        ("约裸聊加微信", "色情"),
        ("自制炸药的方法", "涉枪涉爆"),
        ("法 轮 功", "政治-空格变体"),
        ("支持法轮功", "政治-全角变体"),
    ]

    @pytest.mark.parametrize(
        ("text", "scenario"),
        REJECT_CASES,
        ids=["政治类整条拦截", "色情类整条拦截", "涉枪涉爆整条拦截", "空格变体仍命中", "全角变体仍命中"],
    )
    def test_reject(self, text, scenario):
        """词表命中 → pass=False + action=reject（含归一化变体）"""
        r = check_sensitive(text)
        assert r["pass"] is False, f"[{scenario}] 应拦截: {r}"
        assert r["action"] == "reject"


class TestAdMask:
    """广告类打码保留（整条保留，词替换）"""

    @pytest.mark.parametrize(
        "text_scenario",
        [
            "专业代开发票联系电话",
            "加微信 代 开发 票 优惠",
        ],
        ids=["广告词打码保留", "空格变体原文实际打码"],
    )
    def test_ad_masked(self, text_scenario):
        r = check_sensitive(text_scenario)
        assert r["pass"] is True
        assert r["action"] == "mask"
        assert "*" in r["masked_text"]
        # 广告词字符不得原样残留（P1-13：原文实际打码，防归一化命中但原文 replace 落空）
        assert "代" not in r["masked_text"]
        assert "票" not in r["masked_text"]


class TestNumberMask:
    """号码类打码保留（身份证/手机/银行卡，保留头尾）"""

    NUMBER_CASES = [
        ("我的手机号是13812345678", "138****5678", "手机号打码"),
        ("身份证号11010119900307789X", "110***********789X", "身份证打码-保留前3后4"),
        ("卡号6222021234567890123", "6222***********0123", "银行卡打码-保留前4后4"),
    ]

    @pytest.mark.parametrize(
        ("text", "expect_mask", "scenario"),
        NUMBER_CASES,
        ids=["手机号打码", "身份证打码", "银行卡打码"],
    )
    def test_number_masked(self, text, expect_mask, scenario):
        r = check_sensitive(text)
        assert r["pass"] is True, f"[{scenario}] 号码应打码保留: {r}"
        assert r["action"] == "mask"
        assert expect_mask in r["masked_text"]

    def test_number_in_normal_text(self):
        """长数字（如数量）不打码——16-19 位才算卡号"""
        r = check_sensitive("这个项目预算500万")
        assert r["pass"] is True
        assert r["action"] == "pass"


class TestUrlBlacklist:
    """网址黑名单：域名提取 → 集合查询 → 打码（2026-08-20 接入）"""

    URL_CASES = [
        ("注册这个 0008-qq.cn 网站有优惠", "0008-qq.cn", "黑名单域名打码"),
        ("详情见 https://www.000wyt.com 页面", "000wyt.com", "带协议 URL 打码"),
    ]

    @pytest.mark.parametrize(
        ("text", "forbidden", "scenario"),
        URL_CASES,
        ids=["黑名单域名打码", "带协议 URL 打码"],
    )
    def test_blacklisted_url_masked(self, text, forbidden, scenario):
        r = check_sensitive(text)
        assert r["action"] == "mask", f"[{scenario}] 黑名单网址应打码: {r}"
        assert forbidden not in r["masked_text"]

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

    CATEGORY_CASES = [
        ("去年我们分手了，后来再也没联系", "分手"),
        ("爷爷去年去世了，全家都很伤心", "离世"),
        ("妈妈确诊癌症，下个月住院手术", "健康"),
        ("那年生意破产欠了不少债", "金钱"),
        ("那阵子家里天天吵架，婆媳关系很僵", "家庭矛盾"),
    ]

    @pytest.mark.parametrize(
        ("text", "expect_cat"),
        CATEGORY_CASES,
        ids=["分手类命中", "离世类命中", "健康类命中", "金钱类命中", "家庭矛盾类命中"],
    )
    def test_category_hit(self, text, expect_cat):
        r = check_event_sensitive(text)
        assert expect_cat in r["categories"], f"应命中 {expect_cat}: {r}"

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
            # 清理进程内热加入，避免影响其他用例（R8#12 autouse 快照/恢复兜底）
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

    FILTER_CASES = [
        ("支持法轮功的言论", True),
        ("今天去苏州吃了松鼠桂鱼", False),
        ("去年我们分手了", False),
    ]

    @pytest.mark.parametrize(
        ("text", "expected"),
        FILTER_CASES,
        ids=["硬敏感拦截", "正常文本放行", "事件级软敏感不阻断"],
    )
    def test_filter_sensitive_rule(self, text, expected):
        """规则级过滤只挡硬违规，事件级软敏感不阻断搜索"""
        assert filter_sensitive_rule(text) is expected
