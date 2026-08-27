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


# ---------- P0-1（审查 H1/S4）：生产环境安全兜底 ----------

def test_production_forces_mock_external_ai_false(monkeypatch):
    """生产环境启动期强制 mock_external_ai=False（漏配即 fail-closed，防验证码 mock 直返）"""
    from app.core.config import _apply_production_safety

    s = Settings(
        _env_file=None,
        app_env="production",
        mock_external_ai=True,
        jwt_secret="s" * 40,
        refresh_token_hmac_key="r" * 40,
    )
    _apply_production_safety(s)
    assert s.mock_external_ai is False


def test_development_keeps_mock_flag(monkeypatch):
    """非生产环境不强制（dev/test 联调仍可用 mock）"""
    from app.core.config import _apply_production_safety

    s = Settings(_env_file=None, app_env="development", mock_external_ai=True)
    _apply_production_safety(s)
    assert s.mock_external_ai is True


def test_production_default_jwt_secret_raises(monkeypatch):
    """生产环境默认 JWT 密钥 → RuntimeError（既有 CRITICAL 门禁回归）"""
    import pytest
    from app.core.config import _apply_production_safety

    s = Settings(_env_file=None, app_env="production", jwt_secret="change-me-32-bytes-min-secret-0000")
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        _apply_production_safety(s)


def test_production_default_refresh_hmac_key_raises(monkeypatch):
    """G1/R6#8：生产环境默认 refresh_token_hmac_key → RuntimeError（密钥隔离门禁）"""
    import pytest
    from app.core.config import _apply_production_safety

    s = Settings(
        _env_file=None,
        app_env="production",
        jwt_secret="s" * 40,
        refresh_token_hmac_key="change-me-refresh-hmac-key-0000000000",
    )
    with pytest.raises(RuntimeError, match="REFRESH_TOKEN_HMAC_KEY"):
        _apply_production_safety(s)
