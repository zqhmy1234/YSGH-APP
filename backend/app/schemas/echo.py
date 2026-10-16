"""回响契约（P2-ECHO · 去年今日）"""
from pydantic import BaseModel


class EchoTodayOut(BaseModel):
    """去年今日回响出参（GET /api/v1/echo/today；无命中时 data=null）"""

    content_id: str
    content_type: str
    text: str | None = None
    taken_at: str | None = None    # ISO8601 字符串（服务层 isoformat 输出）
    place: str | None = None
    echo_date: str
    fingerprint: str
    # 2026-10-16：photo 回响缩略图票据 URL（timeline cover_url 同款 Valet Key 直发；
    # 仅 photo 有值，签票失败/非 photo 为 None → 客户端保留渐变占位兜底）
    thumb_url: str | None = None


class EchoDismissOut(BaseModel):
    """回响划掉出参（POST /api/v1/echo/{content_id}/dismiss）"""

    dismissed: bool
    content_id: str
