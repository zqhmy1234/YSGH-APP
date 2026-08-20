"""config.py 别名读取单测（2026-08-19 腾讯云密钥命名对齐）

验证：
  1. 旧命名（TENCENT_SECRET_ID/SECRET_KEY，.env 现状）仍生效
  2. Infisical 存量命名（TENCENT_CI_SECRET_ID/GUANHAIFENG_CI_SECRET_KEY）可回退读取
  3. 两套并存时旧命名优先（AliasChoices 顺序）
  4. dashscope_workspace_id 读取 DASHSCOPE_WORKSPACE_ID
"""
from app.core.config import Settings
from pydantic_settings import BaseSettings


def _fresh_settings(monkeypatch, **env):
    """清掉 .env 干扰，只留传入环境变量构造独立 Settings 实例"""
    monkeypatch.delenv("TENCENT_SECRET_ID", raising=False)
    monkeypatch.delenv("TENCENT_SECRET_KEY", raising=False)
    monkeypatch.delenv("TENCENT_CI_SECRET_ID", raising=False)
    monkeypatch.delenv("TENCENT_GUANHAIFENG_CI_SECRET_KEY", raising=False)
    monkeypatch.delenv("DASHSCOPE_WORKSPACE_ID", raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return Settings(_env_file=None)


def test_old_names_still_work(monkeypatch):
    s = _fresh_settings(monkeypatch, TENCENT_SECRET_ID="old-id", TENCENT_SECRET_KEY="old-key")
    assert s.tencent_secret_id == "old-id"
    assert s.tencent_secret_key == "old-key"


def test_infisical_names_fallback(monkeypatch):
    s = _fresh_settings(
        monkeypatch,
        TENCENT_CI_SECRET_ID="ci-id",
        TENCENT_GUANHAIFENG_CI_SECRET_KEY="ci-key",
    )
    assert s.tencent_secret_id == "ci-id"
    assert s.tencent_secret_key == "ci-key"


def test_priority_old_over_infisical(monkeypatch):
    s = _fresh_settings(
        monkeypatch,
        TENCENT_SECRET_ID="old-id",
        TENCENT_CI_SECRET_ID="ci-id",
        TENCENT_SECRET_KEY="old-key",
        TENCENT_GUANHAIFENG_CI_SECRET_KEY="ci-key",
    )
    assert s.tencent_secret_id == "old-id"
    assert s.tencent_secret_key == "old-key"


def test_both_missing_defaults_empty(monkeypatch):
    s = _fresh_settings(monkeypatch)
    assert s.tencent_secret_id == ""
    assert s.tencent_secret_key == ""


def test_dashscope_workspace_id(monkeypatch):
    s = _fresh_settings(monkeypatch, DASHSCOPE_WORKSPACE_ID="ws-test-123")
    assert s.dashscope_workspace_id == "ws-test-123"


def test_settings_is_basesettings():
    assert issubclass(Settings, BaseSettings)
