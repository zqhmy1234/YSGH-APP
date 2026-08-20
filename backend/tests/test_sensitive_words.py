"""敏感词规则引擎测试（B5b · 2026-08-20 两档升级）

覆盖：
  - 词表类 reject：政治/色情/涉枪爆整条拦截
  - 广告类 mask：打码保留
  - 号码类 mask：身份证/手机/银行卡打码（保留头尾）
  - 归一化：全角/空格变体仍命中
  - 正常文本 pass
"""


from app.services.external.sensitive_words import check_sensitive


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
