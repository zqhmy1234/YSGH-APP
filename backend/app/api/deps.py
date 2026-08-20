"""认证依赖：从 Authorization Bearer 解析当前用户（AUTH-005 access 2h）"""
import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.security import decode_token
from app.db.models import User
from app.db.session import get_db

_bearer = HTTPBearer(auto_error=False)


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """解析 access token → 返回当前用户（无效/过期 → 401）"""
    if credentials is None:
        raise ApiError("AUTH_005", "未提供认证凭据", http=401)

    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError:
        # 修复（审查 MINOR）：收窄为 PyJWTError，不吞其他异常
        raise ApiError("AUTH_005", "token 无效或已过期", http=401) from None

    if payload.get("type") != "access":
        raise ApiError("AUTH_005", "token 类型错误", http=401)

    user = db.get(User, payload.get("sub"))
    if user is None or user.status != 1:
        raise ApiError("AUTH_001", "用户不存在或已冻结", http=401)
    return user
