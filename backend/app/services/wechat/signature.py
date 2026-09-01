"""企微回调验签（官方协议：msg_signature = SHA1(sort(token,timestamp,nonce,encrypt))）

G2/R6#10 加固（2026-08-27）：防重放 + 防伪造。
- verify 强制校验回调 timestamp 落在 now±REPLAY_WINDOW_SECONDS 窗口内：
  签名把 timestamp 绑进 msg_signature（篡改时间戳 → 验签失败），
  而重放旧包携带的是**过期时间戳**（签名无法伪造新时间戳）→ 窗口外拒绝。
  ±窗口同时容忍企微与本站的时钟偏差。企微官方重试会用新 timestamp 重新签名，
  不影响正常重试投递。
"""
from __future__ import annotations

import hashlib
import time

# 回调时间戳防重放窗口（±5 分钟：防重放 + 时钟容差）
REPLAY_WINDOW_SECONDS = 300


def sign(token: str, timestamp: str, nonce: str, encrypt: str) -> str:
    """计算 msg_signature（与官方 SDK 一致）"""
    raw = "".join(sorted([token, timestamp, nonce, encrypt]))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def is_timestamp_fresh(
    timestamp: str, *, window_seconds: int = REPLAY_WINDOW_SECONDS, now: float | None = None
) -> bool:
    """时间戳是否落在 now±window 秒内（Unix 秒；解析失败/越窗 → False，fail-closed）"""
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    current = time.time() if now is None else now
    return abs(current - ts) <= window_seconds


def verify(
    token: str,
    timestamp: str,
    nonce: str,
    encrypt: str,
    msg_signature: str,
    *,
    window_seconds: int = REPLAY_WINDOW_SECONDS,
    now: float | None = None,
) -> bool:
    """验签 + 防重放：签名不匹配 或 时间戳越窗 → False（防伪造 + 防重放回调）

    供 gateway.verify_url / gateway.handle_message 复用（两个回调入口共用本函数，
    timestamp 新鲜度即对 GET URL 验证与 POST 收包同时生效）。
    """
    if sign(token, timestamp, nonce, encrypt) != msg_signature.lower():
        return False
    return is_timestamp_fresh(timestamp, window_seconds=window_seconds, now=now)
