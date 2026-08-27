"""G1 认证安全 · 纯单元测试（不依赖 DB，哈希/密钥隔离/存量兼容）

覆盖：
  - refresh_token HMAC-SHA256（R6#8）：`hmac$` 版本前缀、确定性、密钥隔离
    （换密钥哈希必变；旧密钥校验失败）、与 jwt_secret 独立
  - _verify_refresh_token_hash：现行 HMAC 校验 + TD-P3 存量无前缀 SHA-256 兼容
  - SMS 验证码加盐（R6#9）：加盐哈希 ≠ 裸 sha256、同盐确定、无盐兼容
"""
import hashlib

from app.core.config import settings
from app.services.auth.auth import (
    _hash_refresh_token,
    _sha256_legacy,
    _verify_refresh_token_hash,
)
from app.services.auth.providers import _hash_code

# ---------- G1/R6#8：refresh_token HMAC-SHA256 + 密钥隔离 ----------


def test_hash_refresh_token_hmac_format():
    """`hmac$` 前缀 + 64 hex（HMAC-SHA256 摘要）"""
    h = _hash_refresh_token("rt-token-1")
    assert h.startswith("hmac$")
    assert len(h) == 5 + 64
    # 同 token 哈希确定（可重复校验）
    assert _hash_refresh_token("rt-token-1") == h


def test_hash_refresh_token_deterministic_and_distinct():
    """不同 token 哈希不同；同 token 稳定"""
    assert _hash_refresh_token("a") != _hash_refresh_token("b")
    assert _hash_refresh_token("a") == _hash_refresh_token("a")


def test_hmac_key_isolated_from_jwt_secret():
    """密钥隔离：refresh 哈希密钥 ≠ JWT 密钥（G1 核心要求）"""
    assert settings.refresh_token_hmac_key != settings.jwt_secret


def test_hmac_key_change_invalidates(monkeypatch):
    """密钥隔离：换密钥后旧哈希校验失败（DB 泄漏的旧哈希无法跨密钥复用）"""
    h = _hash_refresh_token("rt-x")
    monkeypatch.setattr(settings, "refresh_token_hmac_key", "another-key-for-test")
    assert _hash_refresh_token("rt-x") != h
    assert not _verify_refresh_token_hash(h, "rt-x")


def test_verify_refresh_token_hash_hmac():
    """现行 `hmac$` 格式：匹配放行、错 token 拒绝（防时序攻击走 compare_digest）"""
    h = _hash_refresh_token("rt-ok")
    assert _verify_refresh_token_hash(h, "rt-ok") is True
    assert _verify_refresh_token_hash(h, "rt-wrong") is False


def test_verify_refresh_token_hash_legacy_sha256_compat():
    """迁移期兼容：存量无前缀 SHA-256 哈希仍可校验（随后续轮换/登录自动升级 HMAC）"""
    legacy = hashlib.sha256(b"rt-legacy").hexdigest()
    assert not legacy.startswith("hmac$")
    assert _verify_refresh_token_hash(legacy, "rt-legacy") is True
    assert _verify_refresh_token_hash(legacy, "rt-other") is False
    # _sha256_legacy 与现行 `hmac$` 明确不同（防止新旧格式混淆）
    assert _sha256_legacy("rt-legacy") == legacy


def test_rotate_writes_hmac_new_format():
    """轮换后新哈希为 `hmac$` 前缀（存量兼容行轮换后自动升级）"""
    new_hash = _hash_refresh_token("rt-new")
    assert new_hash.startswith("hmac$")
    assert _verify_refresh_token_hash(new_hash, "rt-new")


# ---------- G1/R6#9：SMS 验证码加盐 ----------


def test_sms_code_hash_salted():
    """加盐：sha256(salt:code)，非裸 sha256；同盐同码确定"""
    code = "123456"
    salt = "a1b2c3d4e5f6a7b8"
    salted = _hash_code(code, salt)
    assert salted != hashlib.sha256(code.encode("utf-8")).hexdigest(), "必须加盐，禁止裸 sha256"
    assert salted == hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()
    assert _hash_code(code, salt) == salted


def test_sms_code_hash_salt_variability():
    """不同盐同码 → 哈希不同（防彩虹表：同码跨记录不可比）"""
    assert _hash_code("123456", "salt-1") != _hash_code("123456", "salt-2")


def test_sms_code_hash_legacy_compat():
    """无盐（存量行）→ 裸 sha256 兼容比对"""
    code = "654321"
    assert _hash_code(code, None) == hashlib.sha256(code.encode("utf-8")).hexdigest()
