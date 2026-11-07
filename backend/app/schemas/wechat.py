"""企微客服契约（F6 微信入口）"""
from pydantic import BaseModel, Field


class WechatFindOut(BaseModel):
    """微信"找"出参（POST /api/v1/wechat/find）"""

    query: str
    reply: str
    hits: int
    latency_ms: int
    degraded: bool = False


class WechatDeleteOut(BaseModel):
    """微信软删出参（POST /api/v1/wechat/delete）"""

    deleted: bool
    msg_id: str


class WechatBindRequest(BaseModel):
    """微信绑定请求（POST /api/v1/wechat/bind · D09-1）

    openid 为该渠道下的用户标识（企微微信客服回调的 `FromUserName`）；
    channel 取值见 `db/models/wechat.WECHAT_BINDING_CHANNELS`。
    """

    openid: str = Field(..., min_length=1, max_length=128)
    channel: str = Field("wechat_kf", max_length=32)


class WechatBindOut(BaseModel):
    """微信绑定出参（POST /api/v1/wechat/bind）"""

    bound: bool
    openid: str
    channel: str
    user_id: str


class WechatBindingItem(BaseModel):
    """已绑定渠道项（GET /api/v1/wechat/bindings）"""

    openid: str
    channel: str
    bound_at: str | None = None


class WechatBindingsOut(BaseModel):
    bindings: list[WechatBindingItem]


class WechatUnbindOut(BaseModel):
    """解绑出参（DELETE /api/v1/wechat/bindings）"""

    unbound: bool
    openid: str
