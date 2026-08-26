"""内容安全适配器测试（B5b #8 · Wave4-L M3 微信域）

覆盖：
  - provider 工厂切换（默认 tencent_ci；tencent_ci/aliyun/off/rule 可切换；未知回退）
  - off/rule：文本=本地规则（reject 拦截 / 正常放行），图片=放行
  - tencent_ci：文本 delegate moderate（mock 拦截/放行）；图片 CI image_audit
    （mock 拦截/放行 / CI 异常降级放行）
  - aliyun：缺 key → RuntimeError（显式失败）；配 key + mock httpx.post →
    请求 Action/Signature/ServiceParameters 正确 + 响应解析（block/review/pass）
  - 阿里云 RPC 签名正确性（HMAC-SHA1 + RFC3986 双重编码）
"""
import base64
import hashlib
import hmac
import json

import pytest
from app.core.config import settings
from app.services.external import content_safety as cs
from app.services.external.content_safety import (
    AliyunContentSafety,
    OffContentSafety,
    RuleContentSafety,
    TencentCiContentSafety,
    get_content_safety,
)

# ---------------------------------------------------------------------------
# provider 工厂
# ---------------------------------------------------------------------------


def test_default_provider_tencent_ci(monkeypatch):
    monkeypatch.setattr(settings, "content_safety_provider", "tencent_ci")
    assert isinstance(get_content_safety(), TencentCiContentSafety)


def test_factory_switch_providers(monkeypatch):
    for provider, cls in [
        ("tencent_ci", TencentCiContentSafety),
        ("aliyun", AliyunContentSafety),
        ("off", OffContentSafety),
        ("rule", RuleContentSafety),
    ]:
        monkeypatch.setattr(settings, "content_safety_provider", provider)
        assert isinstance(get_content_safety(), cls), provider


def test_factory_unknown_falls_back(monkeypatch):
    monkeypatch.setattr(settings, "content_safety_provider", "bogus")
    assert isinstance(get_content_safety(), TencentCiContentSafety)


# ---------------------------------------------------------------------------
# Wave4-L 新增配置字段（WECHAT_APPID/SECRET、content_safety_provider、阿里云 key）
# ---------------------------------------------------------------------------


def test_content_safety_provider_default():
    from app.core.config import Settings

    assert Settings(_env_file=None).content_safety_provider == "tencent_ci"


def test_new_config_fields_read_from_env(monkeypatch):
    from app.core.config import Settings

    for k in (
        "WECHAT_APPID", "WECHAT_SECRET", "CONTENT_SAFETY_PROVIDER",
        "ALIYUN_ACCESS_KEY_ID", "ALIYUN_ACCESS_KEY_SECRET", "ALIYUN_CONTENT_SAFETY_REGION",
    ):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("WECHAT_APPID", "wx-app")
    monkeypatch.setenv("WECHAT_SECRET", "wx-secret")
    monkeypatch.setenv("CONTENT_SAFETY_PROVIDER", "aliyun")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_ID", "ak-1")
    monkeypatch.setenv("ALIYUN_ACCESS_KEY_SECRET", "sk-1")
    s = Settings(_env_file=None)
    assert s.wechat_appid == "wx-app"
    assert s.wechat_secret == "wx-secret"
    assert s.content_safety_provider == "aliyun"
    assert s.aliyun_access_key_id == "ak-1"
    assert s.aliyun_access_key_secret == "sk-1"
    assert s.aliyun_content_safety_region == "cn-beijing"


def test_aliyun_key_alias_names(monkeypatch):
    """阿里云 key 支持 Infisical 存量名别名（ALIYUN_AK_ID / ALIYUN_AK_SECRET）"""
    from app.core.config import Settings

    for k in ("ALIYUN_ACCESS_KEY_ID", "ALIYUN_ACCESS_KEY_SECRET", "ALIYUN_AK_ID", "ALIYUN_AK_SECRET"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("ALIYUN_AK_ID", "ak-alias")
    monkeypatch.setenv("ALIYUN_AK_SECRET", "sk-alias")
    s = Settings(_env_file=None)
    assert s.aliyun_access_key_id == "ak-alias"
    assert s.aliyun_access_key_secret == "sk-alias"


# ---------------------------------------------------------------------------
# off / rule 实现（本地规则，零外部调用）
# ---------------------------------------------------------------------------


def test_rule_check_text_blocks_sensitive_word():
    assert RuleContentSafety().check_text("法轮功 真相").get("pass") is False
    r = RuleContentSafety().check_text("裸聊群链接")
    assert r["pass"] is False
    assert r["provider"] == "rule"
    assert r["labels"]
    assert RuleContentSafety().check_text("今天天气不错，去公园散步了").get("pass") is True


def test_off_check_text_rules_and_image_pass():
    a = OffContentSafety()
    assert a.check_text("法轮功 真相").get("pass") is False  # 规则仍兜底
    assert a.check_text("正常内容").get("pass") is True
    assert a.check_image("wechat/u/1.jpg").get("pass") is True


def test_rule_check_image_pass():
    assert RuleContentSafety().check_image("k.jpg").get("pass") is True


# ---------------------------------------------------------------------------
# tencent_ci 实现（当前顶替：文本 moderate / 图片 CI image_audit）
# ---------------------------------------------------------------------------


def test_tencent_ci_check_text_uses_moderate(monkeypatch):
    monkeypatch.setattr(
        "app.services.external.moderate",
        lambda text: {"pass": False, "reason": "guard", "matched": ["x"]},
    )
    r = get_content_safety("tencent_ci").check_text("bad")
    assert r["pass"] is False
    assert r["labels"] == ["x"]

    monkeypatch.setattr("app.services.external.moderate", lambda text: {"pass": True, "reason": ""})
    assert get_content_safety("tencent_ci").check_text("ok")["pass"] is True


def test_tencent_ci_check_image(monkeypatch):
    from app.services.external import tencent_ci as tci

    monkeypatch.setattr(tci, "image_audit", lambda k: {"pass": True, "labels": []})
    assert get_content_safety("tencent_ci").check_image("k")["pass"] is True

    monkeypatch.setattr(tci, "image_audit", lambda k: {"pass": False, "labels": ["PornInfo:Porn"]})
    r = get_content_safety("tencent_ci").check_image("k")
    assert r["pass"] is False
    assert "PornInfo:Porn" in r["labels"]


def test_tencent_ci_check_image_fail_open(monkeypatch):
    """CI 不可用 → 默认放行（微信收消息不因审核故障丢消息）"""
    from app.services.external import tencent_ci as tci

    def _boom(k):
        raise RuntimeError("CI down")

    monkeypatch.setattr(tci, "image_audit", _boom)
    assert get_content_safety("tencent_ci").check_image("k")["pass"] is True


# ---------------------------------------------------------------------------
# aliyun 实现（上架前启用；代码先行 + mock 测试）
# ---------------------------------------------------------------------------


def test_aliyun_missing_keys_raises(monkeypatch):
    monkeypatch.setattr(settings, "aliyun_access_key_id", "")
    monkeypatch.setattr(settings, "aliyun_access_key_secret", "")
    with pytest.raises(RuntimeError, match="阿里云内容安全未配置"):
        AliyunContentSafety().check_text("hi")
    with pytest.raises(RuntimeError, match="阿里云内容安全未配置"):
        AliyunContentSafety().check_image("https://x/a.jpg")


def test_aliyun_string_to_sign_double_encode():
    # 阿里云 RPC 签名：参数名/值各自 RFC3986 编码 → 字面 =/& 连接成规范化串 →
    # 整体再百分号编码进 StringToSign（"=" 转 %3D、"&" 转 %26、值内 "%" 转 %25）
    sts = cs._aliyun_string_to_sign("POST", {"Action": "TextModeration"})
    assert sts == "POST&%2F&Action%3DTextModeration"

    sts2 = cs._aliyun_string_to_sign("POST", {"b": "2", "a": "1", "c": "你好"})
    assert sts2.startswith("POST&%2F&")
    assert "a%3D1" in sts2          # 按 key 字典序，分隔符 "=" 编码一次
    assert "b%3D2" in sts2
    assert "c%3D" in sts2
    assert "%25E4%25BD%25A0%25E5%25A5%25BD" in sts2  # 值内 UTF-8 编码再被整体编码


def test_aliyun_signature_hmac_sha1():
    sign_key = "test-secret"
    sts = "POST&%2F&Action%3DTextModeration"
    expected = base64.b64encode(
        hmac.new((sign_key + "&").encode(), sts.encode(), hashlib.sha1).digest()
    ).decode()
    assert cs._aliyun_sign(sign_key, sts) == expected
    assert len(cs._aliyun_sign(sign_key, sts)) == 28  # base64(20 字节) 标准长度


class _FakePost:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


def _enable_aliyun(monkeypatch, payload):
    monkeypatch.setattr(settings, "aliyun_access_key_id", "ak-test")
    monkeypatch.setattr(settings, "aliyun_access_key_secret", "sk-test")
    captured: dict = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        captured["body"] = kwargs.get("json")
        return _FakePost(payload)

    monkeypatch.setattr("httpx.post", fake_post)
    return captured


def test_aliyun_check_text_block(monkeypatch):
    captured = _enable_aliyun(monkeypatch, {
        "Code": 200,
        "Data": {"labels": "political_content", "reason": "x", "riskLevel": "high",
                 "suggestion": "block"},
    })
    r = AliyunContentSafety().check_text("some user text")
    assert r["pass"] is False
    assert "political_content" in r["labels"]
    assert captured["url"].startswith("https://green-cip.")
    assert captured["params"]["Action"] == "TextModeration"
    assert captured["params"]["Signature"]
    assert captured["params"]["ServiceVersion"] == "2022-03-02"
    assert captured["body"]["Service"] == "content_detection"
    assert json.loads(captured["body"]["ServiceParameters"])["content"] == "some user text"


def test_aliyun_check_text_pass_and_review(monkeypatch):
    for suggestion in ("pass", "review"):
        _enable_aliyun(monkeypatch, {
            "Code": 200,
            "Data": {"labels": "", "riskLevel": "medium", "suggestion": suggestion},
        })
        assert AliyunContentSafety().check_text("hi")["pass"] is True


def test_aliyun_check_text_rule_precheck_skips_call(monkeypatch):
    # 规则层先挡（reject）→ 不调阿里云（省费用）
    captured = _enable_aliyun(monkeypatch, {"Code": 200, "Data": {"suggestion": "pass"}})
    r = AliyunContentSafety().check_text("法轮功 真相")
    assert r["pass"] is False
    assert captured == {}  # 未发请求


def test_aliyun_check_image_block(monkeypatch):
    captured = _enable_aliyun(monkeypatch, {
        "Code": 200,
        "Data": {"Result": [
            {"Label": "porn", "Suggestion": "block", "RiskLevel": "high"},
            {"Label": "", "Suggestion": "pass", "RiskLevel": "low"},
        ]},
    })
    r = AliyunContentSafety().check_image("https://cdn.example.com/a.jpg")
    assert r["pass"] is False
    assert "porn" in r["labels"]
    assert captured["params"]["Action"] == "ImageBatchModeration"
    assert json.loads(captured["body"]["ServiceParameters"])["imageUrl"] == "https://cdn.example.com/a.jpg"


def test_aliyun_check_image_pass(monkeypatch):
    _enable_aliyun(monkeypatch, {"Code": 200, "Data": {"Result": []}})
    assert AliyunContentSafety().check_image("https://cdn.example.com/b.jpg")["pass"] is True
