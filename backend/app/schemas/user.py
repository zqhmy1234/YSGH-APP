"""用户域契约（users 表：当前用户信息下发）"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

AccountState = Literal["active", "pending_deletion"]


class AccountStateOut(BaseModel):
    """账号状态（/me 与注销/恢复端点共用）。"""

    account_status: AccountState
    deleted_at: datetime | None = None
    undo_until: datetime | None = Field(None, description="30 天撤销窗口结束时间（UTC）")
    days_left: int | None = Field(None, ge=0, description="撤销窗口剩余天数；active 时 null")


class UserMeOut(AccountStateOut):
    """GET /api/v1/users/me 出参（BB1-B4）

    安全约束：phone 仅服务端掩码形态（138****XXXX）下发，完整手机号绝不出网；
    未绑定 / 非 11 位 → null。pending_deletion 时客户端只应展示恢复入口。
    """

    id: str
    nickname: str | None = None
    avatar: str | None = None
    phone: str | None = Field(None, description="掩码手机号（138****XXXX）；未绑定/非 11 位为 null")


class UserDeletionIn(BaseModel):
    """注销确认体：必须显式传 "delete"，防止客户端误触。"""

    confirm: Literal["delete"]
