"""JWT 认证（决策 #8：access 2h + refresh 30d，自建）"""
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings

ALGORITHM = settings.jwt_algorithm
SECRET = settings.jwt_secret


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_access_token(user_id: str, device_id: str | None = None) -> str:
    """access token：2 小时有效，承载用户身份"""
    expire = _now() + timedelta(minutes=settings.jwt_access_ttl_minutes)
    payload = {
        "sub": user_id,
        "type": "access",
        "exp": expire,
        "iat": _now(),
    }
    if device_id:
        payload["device_id"] = device_id
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


def create_refresh_token(user_id: str, device_id: str) -> str:
    """refresh token：30 天有效，绑定设备（devices 表可吊销）"""
    expire = _now() + timedelta(days=settings.jwt_refresh_ttl_days)
    payload = {
        "sub": user_id,
        "device_id": device_id,
        "type": "refresh",
        "exp": expire,
        "iat": _now(),
    }
    return jwt.encode(payload, SECRET, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    """解码并校验签名/过期；失败抛 jwt.PyJWTError"""
    return jwt.decode(token, SECRET, algorithms=[ALGORITHM])
