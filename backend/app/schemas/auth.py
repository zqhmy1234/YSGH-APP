"""认证契约（决策 #8：微信 unionid 为主 + 手机号验证码备用 + JWT 自建）"""
from pydantic import BaseModel, Field

# ---------- 请求 ----------

class WechatLoginRequest(BaseModel):
    """APP 微信登录：code 换 openid/unionid（服务端调微信接口）"""

    code: str = Field(..., max_length=128, description="微信登录 code（wx.login 获取）")
    device_id: str = Field(..., min_length=1, max_length=64, description="客户端设备唯一 ID")


class PhoneLoginRequest(BaseModel):
    """手机号验证码登录（备用通道）"""

    phone: str = Field(..., pattern=r"^1\d{10}$", description="中国大陆手机号")
    code: str = Field(..., min_length=6, max_length=6, description="短信验证码")


class SendSmsRequest(BaseModel):
    """发送验证码（防刷：限流 + 有效期，测试清单 AUTH-003）"""

    phone: str = Field(..., pattern=r"^1\d{10}$")


class RefreshRequest(BaseModel):
    """refresh token 换新 access（轮换，旧 refresh 失效，AUTH-005）"""

    refresh_token: str


class LogoutRequest(BaseModel):
    """退出登录（G1/R6#7）：吊销该 refresh token 绑定的设备会话（AUTH-006）"""

    refresh_token: str = Field(..., description="要吊销的 refresh token（设备会话标识）")


# ---------- 响应 ----------

class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    access_expires_in: int = 7200      # 2h
    refresh_expires_in: int = 2592000  # 30d
    user: "UserBrief"


class UserBrief(BaseModel):
    id: str
    nickname: str | None = None
    avatar: str | None = None
    is_new_user: bool = False           # 新用户 → 前端引导 AI 冷启动（F7）


class SendSmsOut(BaseModel):
    """发验证码出参（POST /api/v1/auth/sms/send；mock 通道直返验证码，生产直返被 501 拦截）"""

    mock_code: str


class LogoutOut(BaseModel):
    """退出登录出参（POST /api/v1/auth/logout；幂等：无效/吊销失败仍 ok）"""

    ok: bool
