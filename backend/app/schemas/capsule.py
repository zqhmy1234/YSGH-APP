"""时间胶囊 schemas（BA2 批 · 远期总账 A5）

出参附关联 content 摘要（CapsuleContentBrief）——字段全部取自 ContentOut
现成口径（content_type/text/place/taken_at/thumbnail_url），不造新轮子；
缩略图签名 URL 由路由层复用 media_url.content_urls 下发。
"""
from datetime import datetime

from pydantic import BaseModel, Field


class CapsuleCreate(BaseModel):
    """POST /capsules 入参：封存一条记忆到未来"""

    content_id: str = Field(..., min_length=1, description="被封存记忆 id（contents.id）")
    open_at: datetime = Field(..., description="到期时间（必须晚于当前时刻）")
    note: str | None = Field(None, max_length=2000, description="给未来自己的话")


class CapsuleContentBrief(BaseModel):
    """胶囊关联记忆摘要（ContentOut 现成字段投影）"""

    id: str
    content_type: str
    text: str | None = None
    place: str | None = None
    taken_at: datetime | None = None
    thumbnail_url: str | None = None


class CapsuleOut(BaseModel):
    """胶囊出参（列表/详情/开启通用；status: sealed/due/opened）"""

    id: str
    content_id: str
    note: str | None = None
    sealed_at: datetime
    open_at: datetime
    opened_at: datetime | None = None
    status: str
    content: CapsuleContentBrief | None = None


class CapsuleDeleteOut(BaseModel):
    """DELETE /capsules/{id} 出参：撤销封存"""

    capsule_id: str
    deleted: bool
