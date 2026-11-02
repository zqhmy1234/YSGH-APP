"""画像重建 API 契约。"""
from pydantic import BaseModel, Field


class ProfileRebuildStartOut(BaseModel):
    job_id: str
    status: str = "queued"


class ProfileRebuildStatusOut(BaseModel):
    job_id: str
    status: str
    # 2026-10-30（P2 F-6）：`total` = 该用户**全部**待重建内容的真实条数（count 查询），
    # 不再是"本批最多 500 条"。客户端可用 `processed/total` 表示真实进度。
    total: int = 0
    processed: int = 0
    applied: int = 0
    pooled: int = 0
    failed: int = 0
    skipped: int = 0
    # 已处理的批次序号（每批 `limit` 条；用于判断"还在推进"而不是卡住）
    batches: int = 0
    degraded: bool = False
    error: str | None = None
    summary: dict = Field(default_factory=dict)
