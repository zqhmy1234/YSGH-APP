"""用户路由（BB1 + 账号注销/恢复）

GET  /api/v1/users/me          —— 当前用户信息（含账号状态；待注销窗口内仍可读）
DELETE /api/v1/users/me        —— 申请注销：软删内容 + 关闭普通接口，30 天内可恢复
POST /api/v1/users/me/restore  —— 撤销注销：恢复内容与账号，窗口外返回 410

手机号在服务端掩码（11 位保留前 3 后 4：138****XXXX），完整号码绝不出网。
"""
import math
from datetime import UTC, datetime

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user_or_pending_deletion
from app.core.errors import (
    ERR_USER_001,
    ERR_USER_002,
    ERR_USER_003,
    ApiError,
)
from app.db.models import User
from app.db.session import get_db
from app.domain.memory.purge import (
    account_undo_until,
    request_account_purge,
    restore_account_purge,
)
from app.schemas.common import ApiResponse
from app.schemas.user import AccountStateOut, UserDeletionIn, UserMeOut

router = make_router(prefix="/api/v1/users", tags=["users"])


def _mask_phone(phone: str | None) -> str | None:
    """手机号掩码（服务端下发前处理）：11 位保留前 3 后 4；其余形态一律 None 不下发"""
    if phone is None:
        return None
    digits = phone.strip()
    if len(digits) != 11:
        return None
    return digits[:3] + "****" + digits[7:]


def _account_state(user: User, undo_until: datetime | None = None) -> AccountStateOut:
    """User → 账号状态；pending 时剩余天数按“不足一天算 1 天”展示。"""
    if user.deleted_at is None:
        return AccountStateOut(account_status="active")
    until = undo_until or account_undo_until(user.deleted_at)
    if until.tzinfo is None:
        until = until.replace(tzinfo=UTC)
    left = max(0, math.ceil((until - datetime.now(UTC)).total_seconds() / 86400))
    return AccountStateOut(
        account_status="pending_deletion",
        deleted_at=user.deleted_at,
        undo_until=until,
        days_left=left,
    )


@router.get("/me", response_model=ApiResponse[UserMeOut])
def users_me(user: User = Depends(get_current_user_or_pending_deletion)):
    """当前用户信息：正常账号与待注销账号都可读，客户端据此展示恢复入口。"""
    state = _account_state(user)
    return ApiResponse(
        data=UserMeOut(
            id=str(user.id),
            nickname=user.nickname,
            avatar=user.avatar,
            phone=_mask_phone(user.phone),
            **state.model_dump(),
        )
    )


@router.delete("/me", response_model=ApiResponse[AccountStateOut])
def users_request_deletion(
    payload: UserDeletionIn,
    user: User = Depends(get_current_user_or_pending_deletion),
    db: Session = Depends(get_db),
):
    """申请注销（幂等）：必须显式 `{"confirm":"delete"}`；30 天内不物理删除。"""
    del payload  # 只用于强制客户端显式确认，值由 Literal 校验
    receipt = request_account_purge(db, str(user.id), actor_id=str(user.id))
    if receipt.status == "failed":
        raise ApiError(ERR_USER_001, "账号注销处理失败，请稍后重试", http=500)
    db.refresh(user)
    return ApiResponse(data=_account_state(user, receipt.undo_until))


@router.post("/me/restore", response_model=ApiResponse[AccountStateOut])
def users_restore_account(
    user: User = Depends(get_current_user_or_pending_deletion),
    db: Session = Depends(get_db),
):
    """撤销注销：恢复软删内容与账号；窗口过期由依赖返回 410 USER_002。"""
    receipt = restore_account_purge(db, str(user.id))
    if receipt.status == "failed":
        if "undo_window_expired" in receipt.warnings:
            raise ApiError(ERR_USER_002, "账号注销恢复窗口已过", http=410)
        if "no_pending_account_purge" in receipt.warnings:
            raise ApiError(ERR_USER_003, "账号未处于可恢复的注销状态", http=409)
        raise ApiError(ERR_USER_001, "账号恢复处理失败，请稍后重试", http=500)
    db.refresh(user)
    return ApiResponse(data=_account_state(user))
