"""企微回调验签（官方协议：msg_signature = SHA1(sort(token,timestamp,nonce,encrypt))）"""
from __future__ import annotations

import hashlib


def sign(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    """计算 msg_signature（与官方 SDK 一致）"""
    raw = "".join(sorted([token, timestamp, nonce, encrypt]))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def verify(token: str, timestamp: str, nonce: str, encrypt: str, msg_signature: str) -> bool:
    """验签：签名不匹配返回 False（防伪造回调）"""
    return sign(token, timestamp, nonce, encrypt) == msg_signature.lower()
