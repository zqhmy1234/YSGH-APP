"""B-3 crypto properties, including fail-closed key handling."""

import base64
import os

import pytest
from app.core.config import Settings, _apply_production_safety, settings
from app.services.profile_sensitive_crypto import decrypt_topic, encrypt_topic, topic_digest
from cryptography.exceptions import InvalidTag


def test_randomized_ciphertext_and_stable_normalized_digest(profile_crypto_keys):
    first, digest = encrypt_topic(" 前女友 ")
    second, same_digest = encrypt_topic("前女友")
    assert first != second
    assert digest == same_digest == topic_digest("前女友")
    assert decrypt_topic(first) == "前女友"
    assert "前女友" not in first
    assert topic_digest("ＡＢＣ") == topic_digest("abc")


def test_ciphertext_tampering_is_rejected(profile_crypto_keys):
    ciphertext, _ = encrypt_topic("不要提前任")
    raw = bytearray(base64.b64decode(ciphertext[3:]))
    raw[-1] ^= 1
    with pytest.raises(InvalidTag):
        decrypt_topic("v1:" + base64.b64encode(raw).decode())


def test_missing_or_shared_keys_fail_closed(monkeypatch):
    monkeypatch.setattr(settings, "profile_field_encryption_key", "")
    monkeypatch.setattr(settings, "profile_field_hmac_key", "")
    with pytest.raises(RuntimeError, match="PROFILE_FIELD_ENCRYPTION_KEY"):
        encrypt_topic("话题")
    key = base64.b64encode(os.urandom(32)).decode()
    monkeypatch.setattr(settings, "profile_field_encryption_key", key)
    monkeypatch.setattr(settings, "profile_field_hmac_key", key)
    with pytest.raises(RuntimeError, match="must differ"):
        encrypt_topic("话题")


def test_production_requires_profile_keys(monkeypatch):
    monkeypatch.delenv("PROFILE_FIELD_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("PROFILE_FIELD_HMAC_KEY", raising=False)
    config = Settings(
        _env_file=None, app_env="production", jwt_secret="j" * 40,
        refresh_token_hmac_key="r" * 40,
        profile_field_encryption_key="", profile_field_hmac_key="",
    )
    with pytest.raises(RuntimeError, match="PROFILE_FIELD"):
        _apply_production_safety(config)
