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


# ---------------------------------------------------------------------------
# 2026-10-24（B-15 残余风险收口）：`app_env` 必须归一化
#
# 背景（voice-agent 实测发现）：全仓 6 处生产守卫都用 `app_env == "production"`
# **字符串精确比较**。实测 `APP_ENV=Production` / `PRODUCTION` / `prod` / `prod `（带空格）
# **全部不命中** ⇒ 那些「生产环境必须 fail-closed」的守卫会**静默失效**
# （B-15 的两道 mock 守卫首当其冲：mock 猜测会重新入库）。
#
# 修在配置层（一处覆盖 6 个调用点），而不是逐个 `.lower()`：
# 逐处改的话，每加一个新守卫就多一次漏改机会 —— 那正是本缺陷的成因。
# ---------------------------------------------------------------------------
import pytest as _pytest2  # noqa: E402


@_pytest2.mark.parametrize(
    "raw",
    ["production", "Production", "PRODUCTION", "prod", "PrOd", " prod ", "prod\n", "PRD"],
)
def test_app_env_aliases_normalize_to_production(raw):
    """各种生产写法都必须归一化成 `production`，否则 6 处守卫静默失效。"""
    s = Settings(_env_file=None, app_env=raw)
    assert s.app_env == "production", f"{raw!r} 未归一化 ⇒ 生产守卫会静默失效"


@_pytest2.mark.parametrize("raw", ["development", "Development", "dev", "DEV", "local", " dev "])
def test_app_env_aliases_normalize_to_development(raw):
    """各种开发写法归一化成 `development`（否则本地会误判成生产、起不来）。"""
    s = Settings(_env_file=None, app_env=raw)
    assert s.app_env == "development"


@_pytest2.mark.parametrize("raw", ["test", "staging", "qa"])
def test_unknown_app_env_is_preserved_lowercased(raw):
    """未知环境名**不猜**：只去空白 + 转小写，原样保留。

    为什么不猜成 production：会让 CI/预发的开发流程起不来；
    为什么不猜成 development：会让真实生产 fail-open。两者都比「如实保留」更糟。
    """
    s = Settings(_env_file=None, app_env=f"  {raw.upper()}  ")
    assert s.app_env == raw


def test_normalized_production_still_enforces_guards():
    """归一化必须真正让守卫生效（端到端，不只是字段值对）。"""
    from app.core.config import _apply_production_safety

    for raw in ("Production", "PRODUCTION", "prod"):
        s = Settings(
            _env_file=None,
            app_env=raw,
            jwt_secret="s" * 40,
            refresh_token_hmac_key="r" * 40,
            profile_field_encryption_key=_b64.b64encode(_os.urandom(32)).decode(),
            profile_field_hmac_key=_b64.b64encode(_os.urandom(32)).decode(),
            mock_external_ai=True,
        )
        _apply_production_safety(s)
        assert s.mock_external_ai is False, f"{raw!r} 未触发生产守卫（mock 会直返假凭证）"
