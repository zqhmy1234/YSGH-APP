"""认证依赖：Bearer 当前用户（AUTH-005）+ 共享 UUID 校验（R4#2）+ 共享分页参数（R4#7）
+ 共享归属校验 loader（波D ②：按 id 取本人实体的统一入口）
+ 服务间调用鉴权（W2 检索升级 · 2026-10-06）
"""
import logging
import secrets
import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ERR_AUTH_001, ERR_AUTH_005, ERR_CONTENT_010, ApiError
from app.core.security import decode_token
from app.db.models import Content, User
from app.db.session import get_db
from app.services.errors import NotFoundError

_bearer = HTTPBearer(auto_error=False)
_logger = logging.getLogger(__name__)


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


# ---------------------------------------------------------------------------
# 服务间调用鉴权（W2 检索升级 · 2026-10-06）
# ---------------------------------------------------------------------------

# 头名常量。⚠️ 刻意不含 "token" 字样：ruff S105 会把「名为 *token* 的变量被赋
# 字符串字面量」判为硬编码密码，而这里是 **HTTP 头的名字**，不是凭据值。
_HDR_AGENT_AUTH = "X-Agent-Token"
_HDR_AGENT_USER = "X-On-Behalf-Of"


def agent_service_user(
    request: Request,
    db: Session = Depends(get_db),
) -> User:
    """以「服务间共享密钥 + 代执行用户」的方式解析出一个 User（agent 检索专用）。

    ## 为什么需要这条独立通道

    agent 服务按设计**不持有用户登录态**（`config.py:62-63`：客户端只认本后端的
    JWT，user_id 由本后端反代注入）。于是 agent 要复用 backend 的 RAG 检索时，
    拿不到任何用户的 JWT —— 而检索端点必须按 user_id 隔离。既有配置里
    `agent_service_token` 是 backend→agent 方向的共享密钥（出站），本依赖把它
    反向消费一次，形成双向互信：**同一把密钥、两个方向**，零新增配置。

    ## 三个安全决定

    1. **fail-closed**：`agent_service_token` 为空（本地未配置）⇒ 503 拒绝，
       **绝不**像 agent 侧的 `X-Agent-Token` 那样本地放行。理由：这是 backend 的
       入站面，是安全边界；一旦放行，任何知道端点的人都能以任意 user_id 检索
       他人记忆。dev 想用这条通道，就把两个服务的 token 配成同一个值。
    2. **恒定时间比较**（`secrets.compare_digest`）：令牌比对不能给时序侧信道留口子。
    3. **代执行用户必须真实存在且未冻结**：与 `get_current_user` 的校验完全同构
       （`status != 1` 拒绝），不存在"拿一个编造的 user_id 就能查"的路径。

    ## 使用范围（刻意收窄）

    只挂在 `POST /api/v1/search/agent`（`include_in_schema=False`，不入 OpenAPI）。
    **不要**挂到全局依赖——这条通道能以任意用户身份行事，暴露面必须最小。
    """
    expected = settings.agent_service_token
    if not expected:
        raise ApiError(ERR_AUTH_005, "服务间通道未配置（agent_service_token 为空）", http=503)

    provided = request.headers.get(_HDR_AGENT_AUTH) or ""
    if not provided or not secrets.compare_digest(provided, expected):
        _logger.warning("agent 服务间调用令牌无效")
        raise ApiError(ERR_AUTH_005, "服务间令牌无效", http=401)

    user_id = (request.headers.get(_HDR_AGENT_USER) or "").strip()
    if not user_id:
        raise ApiError(ERR_AUTH_005, f"缺少 {_HDR_AGENT_USER} 头", http=401)

    user = db.get(User, user_id)
    if user is None or user.status != 1:
        raise ApiError(ERR_AUTH_001, "代理用户不存在或已冻结", http=401)
    return user


# ---------------------------------------------------------------------------
# 共享归属校验 loader（波D ② 收敛；原 contents.py:_load_alive_content 提升为公共）
# ---------------------------------------------------------------------------


def load_owned_entity(
    db: Session,
    model,
    entity_id,
    user_id: str,
    error_code: str,
    message: str,
    *,
    alive: bool = False,
    http: int = 404,
):
    """「按 id 取本人既有实体」的泛化 loader（B3′ 收敛 · 2026-09-24）

    波D ② 只把 contents 域的 loader 提升到 deps.py；content / capsule / message
    三域仍各留一份「同一 where 结构 + 同一 404 语义」的复制体（深审 D06-1）。
    本函数收敛该结构：候选条件恒为 `id == entity_id AND user_id == user_id`
    （`alive=True` 时附加 `deleted_at IS NULL`），未命中统一抛 404，
    **不区分「不存在」与「非本人」**（IDOR 防护：不泄露存在性）。

    错误码与文案由调用方逐字传入 —— 各域既有可观测语义（错误码/文案/HTTP）零变化。
    """
    conditions = [model.id == entity_id, model.user_id == user_id]
    if alive:
        conditions.append(model.deleted_at.is_(None))
    row = db.execute(select(model).where(*conditions)).scalar_one_or_none()
    if row is None:
        raise ApiError(error_code, message, http=http)
    return row


def load_alive_content(db: Session, user_id: str, content_id: str):
    """取当前用户未删除内容；不存在/已删除/非本人 → 统一 404 CONTENT_010（IDOR 防护不区分三种情形）

    波D ② 收敛（2026-09-10）：本函数原为 `api/contents.py::_load_alive_content`（模块私有），
    被 contents 域 7 个端点复用（detail / PATCH / DELETE / favorite×3 / waveform）。
    提升到 deps.py 作为「按 content_id 取本人既有实体」的唯一入口，供跨域复用
    （capsules.seal_capsule 同款语义——ERR_CONTENT_010 + 同名文案）。
    语义与提升前逐字等价：同一查询条件、同一错误码、同一 message、同一 HTTP 404。

    B3′（2026-09-24）：结构委托 `load_owned_entity`（alive=True）——语义逐字不变。
    """
    return load_owned_entity(
        db,
        Content,
        content_id,
        user_id,
        ERR_CONTENT_010,
        "内容不存在或无权访问",
        alive=True,
    )
