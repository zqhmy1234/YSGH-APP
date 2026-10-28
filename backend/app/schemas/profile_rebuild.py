"""画像重建 API 契约。"""
from pydantic import BaseModel, Field


class ProfileRebuildStartOut(BaseModel):
    job_id: str
    status: str = "queued"


class ProfileRebuildStatusOut(BaseModel):
    job_id: str
    status: str
    total: int = 0
    processed: int = 0
    applied: int = 0
    pooled: int = 0
    failed: int = 0
    skipped: int = 0
    degraded: bool = False
    error: str | None = None
    summary: dict = Field(default_factory=dict)
