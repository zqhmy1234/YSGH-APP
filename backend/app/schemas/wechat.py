"""企微客服契约（F6 微信入口）"""
from pydantic import BaseModel


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
