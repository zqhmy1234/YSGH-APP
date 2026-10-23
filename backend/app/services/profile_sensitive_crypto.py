"""Encryption and blind indexing for profile_sensitive.topic.

The API keeps the user's spelling. Only the HMAC input is normalized: NFKC,
strip surrounding whitespace, then casefold. Key rotation needs a separate
data migration; replacing either key in place would make stored rows unusable.
"""

import base64
import binascii
import hashlib
import hmac
import os
import unicodedata

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import settings

_AAD = b"profile_sensitive.topic:v1"
_PREFIX = "v1:"


def _key(value: str, name: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise RuntimeError(f"{name} must be base64-encoded 32 random bytes") from exc
    if len(raw) != 32:
        raise RuntimeError(f"{name} must be base64-encoded 32 random bytes")
    return raw


def validate_profile_field_keys() -> tuple[bytes, bytes]:
    """Fail closed in every environment once this feature is used."""
    encryption_key = _key(settings.profile_field_encryption_key, "PROFILE_FIELD_ENCRYPTION_KEY")
    hmac_key = _key(settings.profile_field_hmac_key, "PROFILE_FIELD_HMAC_KEY")
    if hmac.compare_digest(encryption_key, hmac_key):
        raise RuntimeError("PROFILE_FIELD_ENCRYPTION_KEY and PROFILE_FIELD_HMAC_KEY must differ")
    return encryption_key, hmac_key


def normalize_topic(topic: str) -> str:
    return unicodedata.normalize("NFKC", topic).strip().casefold()


def topic_digest(topic: str) -> str:
    normalized = normalize_topic(topic)
    if not normalized:
        raise ValueError("topic 不能为空")
    _, hmac_key = validate_profile_field_keys()
    return hmac.new(hmac_key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def encrypt_topic(topic: str) -> tuple[str, str]:
    topic = topic.strip()
    if not topic:
        raise ValueError("topic 不能为空")
    encryption_key, _ = validate_profile_field_keys()
    nonce = os.urandom(12)
    encrypted = AESGCM(encryption_key).encrypt(nonce, topic.encode("utf-8"), _AAD)
    return _PREFIX + base64.b64encode(nonce + encrypted).decode("ascii"), topic_digest(topic)


def decrypt_topic(ciphertext: str) -> str:
    if not ciphertext or not ciphertext.startswith(_PREFIX):
        raise ValueError("Unsupported profile topic ciphertext")
    encryption_key, _ = validate_profile_field_keys()
    try:
        data = base64.b64decode(ciphertext[len(_PREFIX):], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Invalid profile topic ciphertext") from exc
    if len(data) < 29:  # 12-byte nonce + at least 1 byte of text + 16-byte tag
        raise ValueError("Invalid profile topic ciphertext")
    return AESGCM(encryption_key).decrypt(data[:12], data[12:], _AAD).decode("utf-8")
