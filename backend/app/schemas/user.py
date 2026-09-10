"""用户域契约（users 表：当前用户信息下发）"""
from pydantic import BaseModel, Field


class UserMeOut(BaseModel):
    """GET /api/v1/users/me 出参（BB1-B4）

    安全约束：phone 仅服务端掩码形态（138****XXXX）下发，完整手机号绝不出网；
    未绑定 / 非 11 位 → null。
    """

    id: str
    nickname: str | None = None
    avatar: str | None = None
    phone: str | None = Field(None, description="掩码手机号（138****XXXX）；未绑定/非 11 位为 null")
