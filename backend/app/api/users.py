"""用户路由（BB1）：当前用户信息

GET /api/v1/users/me：返回当前登录用户基本信息。手机号在服务端掩码
（11 位保留前 3 后 4：138****XXXX），完整号码绝不出网（安全约束）。
"""
from fastapi import Depends

from app.api import make_router
from app.api.deps import get_current_user
from app.db.models import User
from app.schemas.common import ApiResponse
from app.schemas.user import UserMeOut

router = make_router(prefix="/api/v1/users", tags=["users"])


def _mask_phone(phone: str | None) -> str | None:
    """手机号掩码（服务端下发前处理）：11 位保留前 3 后 4；其余形态一律 None 不下发"""
    if phone is None:
        return None
    digits = phone.strip()
    if len(digits) != 11:
        return None
    return digits[:3] + "****" + digits[7:]


@router.get("/me", response_model=ApiResponse[UserMeOut])
def users_me(user: User = Depends(get_current_user)):
    """当前用户信息（BB1-B4）：id/nickname/avatar + 掩码手机号（未绑定 → null）"""
    return ApiResponse(
        data=UserMeOut(
            id=str(user.id),
            nickname=user.nickname,
            avatar=user.avatar,
            phone=_mask_phone(user.phone),
        )
    )
