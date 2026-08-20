"""JWT 认证（决策 #8：access 2h + refresh 30d，自建）"""
import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.core.config import settings

# 审查修复(P2-04)：SECRET/ALGORITHM 改为函数内读取——消除导入期快照
# （运行时改 settings.jwt_secret 不生效、测试无法用不同密钥实例化）


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _jwt_params() -> tuple[str, str]:
    return settings.jwt_secret, settings.jwt_algorithm


def create_access_token(user_id: str, device_id: str | None = None) -> str:
    """access token：2 小时有效，承载用户身份（jti 防重放）"""
    secret, algorithm = _jwt_params()
    expire = _now() + timedelta(minutes=settings.jwt_access_ttl_minutes)
    payload = {
        "jti": uuid.uuid4().hex,
        "sub": user_id,
        "type": "access",
        "exp": expire,
        "iat": _now(),
    }
    if device_id:
        payload["device_id"] = device_id
    return jwt.encode(payload, secret, algorithm=algorithm)


def create_refresh_token(user_id: str, device_id: str) -> str:
    """refresh token：30 天有效，绑定设备（devices 表可吊销）；jti 保证轮换后必不同"""
    secret, algorithm = _jwt_params()
    expire = _now() + timedelta(days=settings.jwt_refresh_ttl_days)
    payload = {
        "jti": uuid.uuid4().hex,
        "sub": user_id,
        "device_id": device_id,
        "type": "refresh",
        "exp": expire,
        "iat": _now(),
    }
    return jwt.encode(payload, secret, algorithm=algorithm)


def decode_token(token: str) -> dict:
    """解码并校验签名/过期；失败抛 jwt.PyJWTError"""
    secret, algorithm = _jwt_params()
    return jwt.decode(token, secret, algorithms=[algorithm])
