"""认证依赖：Bearer 当前用户（AUTH-005）+ 共享 UUID 校验（R4#2）+ 共享分页参数（R4#7）"""
import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.errors import ERR_AUTH_001, ERR_AUTH_005, ApiError
from app.core.security import decode_token
from app.db.models import User
from app.db.session import get_db
from app.services.errors import NotFoundError

_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class PageParams:
    """共享分页查询参数（R4#6/#7：cursor 定标 API-006；limit 上限统一）"""

    limit: int
    cursor: str | None


def pagination_params(
    limit: int = Query(20, ge=1, le=100, description="每页条数（1-100）"),
    cursor: str | None = Query(None, description="分页游标（不透明字符串，由上次响应返回）"),
) -> PageParams:
    """共享分页参数依赖：contents/messages 列表统一套用（消除各端点手工 clamp 与 int/str 游标混用）

    - limit 由 FastAPI Query 校验（1..100），替代端点内 `min(max(limit,1),N)` 手写截断
    - cursor 统一为不透明字符串（对齐 Page.cursor: str | None；messages 内部按 id 解析）
    """
    return PageParams(limit=limit, cursor=cursor)


def uuid4_str(value: str) -> str:
    """共享 UUID v4 校验（R4#2 · 重构侦察 R4-P1#2）：非法 → NotFoundError（api 层映射 404）

    语义与"资源不存在"一致（畸形 ID 视同不存在，不暴露内部错误路径、不触发
    psycopg2 DataError → 500）；API 层路径/表单/查询参数与服务层入口统一套用。
    返回规范小写字符串（str(uuid.UUID(...))）。
    """
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError) as exc:
        raise NotFoundError(f"无效的 ID: {value}") from exc


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """解析 access token → 返回当前用户（无效/过期 → 401）"""
    if credentials is None:
        raise ApiError(ERR_AUTH_005, "未提供认证凭据", http=401)

    try:
        payload = decode_token(credentials.credentials)
    except jwt.PyJWTError:
        # 修复（审查 MINOR）：收窄为 PyJWTError，不吞其他异常
        raise ApiError(ERR_AUTH_005, "token 无效或已过期", http=401) from None

    if payload.get("type") != "access":
        raise ApiError(ERR_AUTH_005, "token 类型错误", http=401)

    user = db.get(User, payload.get("sub"))
    if user is None or user.status != 1:
        raise ApiError(ERR_AUTH_001, "用户不存在或已冻结", http=401)
    return user
