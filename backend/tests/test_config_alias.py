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
    import base64
    import os

    from app.core.config import _apply_production_safety

    s = Settings(
        _env_file=None,
        app_env="production",
        mock_external_ai=True,
        jwt_secret="s" * 40,
        refresh_token_hmac_key="r" * 40,
        profile_field_encryption_key=base64.b64encode(os.urandom(32)).decode(),
        profile_field_hmac_key=base64.b64encode(os.urandom(32)).decode(),
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


# ---------------------------------------------------------------------------
# 2026-10-24（待办总账 A-01 风险 1 闭环）：生产密钥校验从「只比对默认值」扩到四档
#
# 背景实测：原实现下 `Settings(app_env='production', jwt_secret='')` 能一路通过
# `_apply_production_safety`，带着**空 HMAC 密钥**启动 —— 空密钥下任意 token 都可被
# 离线伪造，比"用默认值"更严重（默认值至少还是个已知常量，空值等于把签名关掉）。
# 这组用例同时是**负向探针**：每条都必须真的被拒，否则说明校验又退化回只查默认值。
# ---------------------------------------------------------------------------
import base64 as _b64  # noqa: E402
import os as _os  # noqa: E402

import pytest as _pytest  # noqa: E402


def _prod_settings(**overrides):
    """构造一份「除被测项外都合法」的生产配置。"""
    base = {
        "_env_file": None,
        "app_env": "production",
        "jwt_secret": "s" * 40,
        "refresh_token_hmac_key": "r" * 40,
        "profile_field_encryption_key": _b64.b64encode(_os.urandom(32)).decode(),
        "profile_field_hmac_key": _b64.b64encode(_os.urandom(32)).decode(),
    }
    base.update(overrides)
    return Settings(**base)


@_pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("JWT 空串", {"jwt_secret": ""}),
        ("JWT 默认值", {"jwt_secret": "change-me-32-bytes-min-secret-0000"}),
        ("JWT 过短 16", {"jwt_secret": "s" * 16}),
        ("refresh 空串", {"refresh_token_hmac_key": ""}),
        ("refresh 默认值", {"refresh_token_hmac_key": "change-me-refresh-hmac-key-0000000000"}),
        ("refresh 过短 16", {"refresh_token_hmac_key": "r" * 16}),
        ("jwt 与 refresh 相同", {"refresh_token_hmac_key": "s" * 40}),
    ],
)
def test_production_rejects_weak_secrets(label, overrides):
    """生产环境：空值 / 默认值 / 过短 / 两把相同 —— 一律 fail-closed。"""
    from app.core.config import _apply_production_safety

    s = _prod_settings(**overrides)
    with _pytest.raises(RuntimeError):
        _apply_production_safety(s)


def test_production_accepts_strong_secrets():
    """合法配置（两把独立 40 字节）必须通过——防「校验改得永远红」。"""
    from app.core.config import _apply_production_safety

    s = _prod_settings()
    _apply_production_safety(s)  # 不抛即通过
    assert len(s.jwt_secret) >= 32
    assert s.jwt_secret != s.refresh_token_hmac_key


def test_development_allows_empty_secrets():
    """dev/test 保持宽松（否则本地起服务都要先造密钥）。"""
    from app.core.config import _apply_production_safety

    s = Settings(_env_file=None, app_env="development", jwt_secret="", refresh_token_hmac_key="")
    _apply_production_safety(s)  # 不抛即通过
