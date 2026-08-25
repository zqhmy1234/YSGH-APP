"""内容契约（contents 表：照片/文字/语音/文章统一入库）"""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ContentCreate(BaseModel):
    content_type: str = Field(..., pattern=r"^(photo|text|voice|article)$")
    text: str | None = None               # OCR 结果/转写/原文
    taken_at: datetime | None = None
    gps_lat: float | None = Field(None, ge=-90, le=90)
    gps_lng: float | None = Field(None, ge=-180, le=180)
    perceptual_hash: str | None = None    # 去重（Q16）
    cos_key: str | None = None            # 照片原件（STS 直传后回调）
    thumbnail_key: str | None = None
    extra: dict[str, Any] | None = None   # EXIF/时长/尺寸
    source: str = Field("app", pattern=r"^(app|windows|wechat|import)$")


class ContentOut(BaseModel):
    id: str
    content_type: str
    content_class: str | None
    text: str | None
    taken_at: datetime | None
    place: str | None
    emotion: dict | None
    tags: list[str] = []
    status: str
    audio_processing: dict[str, Any] | None = None
    created_at: datetime


class ContentUploadResult(BaseModel):
    """上传结果：直传 COS 场景返回 STS 凭证 + 回调参数"""

    content_id: str
    status: str
    cos_presign: "CosPresign | None" = None


class ProfileSensitiveCreate(BaseModel):
    """画像级敏感增/改（B1-6 对话式：POST /api/v1/profile/sensitive）"""

    topic: str = Field(..., min_length=1, max_length=64, description="敏感话题（用户原话/短语）")
    disposition: str = Field(
        "forbid",
        pattern=r"^(allow|mention|caution|review|forbid)$",
        description="处置级别：allow/mention/caution/review/forbid",
    )
    evidence: list[str] | None = Field(None, description="证据（用户原话等）")
    locked: bool = Field(False, description="用户显式标记（永不过期语义强化）")


class ProfileSensitiveOut(BaseModel):
    """画像级敏感输出（B1-6：GET /api/v1/profile/sensitive）"""

    id: int
    topic: str
    disposition: str
    evidence: list
    locked: bool
    added_at: datetime
    updated_at: datetime


class CosPresign(BaseModel):
    """STS 临时密钥 + 上传路径（决策 #10：30 秒有效）"""

    tmp_secret_id: str
    tmp_secret_key: str
    session_token: str
    expired_at: datetime
    cos_key: str
