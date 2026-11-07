"""分片上传契约（S5-03 · WP-C）"""
from datetime import datetime

from pydantic import BaseModel


class UploadInitOut(BaseModel):
    """建上传任务出参（POST /api/v1/upload/init）"""

    upload_id: str
    chunk_size: int
    chunk_count: int
    file_key: str
    status: str
    upload_mode: str
    on_wifi: bool | None = None


class UploadChunkOut(BaseModel):
    """传单片出参（PUT/POST /api/v1/upload/chunk）

    status ∈ uploaded / duplicate / completed；completed 时带 detail，uploaded/duplicate 带 chunk_index。
    """

    status: str
    chunk_index: int | None = None
    detail: str | None = None


class UploadCompleteOut(BaseModel):
    """合并出参（POST /api/v1/upload/complete）——分片未齐拒绝 409，超限 413"""

    status: str
    file_key: str
    upload_id: str
    content_id: str | None = None    # 集成侧：建/复用 contents 记录后回填
    # 后端最终确认的拍摄事实；客户端据此覆盖本地临时值，再生成端侧事件。
    taken_at: datetime | None = None
    gps_lat: float | None = None
    gps_lng: float | None = None


class UploadStatusOut(BaseModel):
    """断点续传状态出参（GET /api/v1/upload/status）"""

    upload_id: str
    status: str
    chunk_count: int
    uploaded_chunks: list[int]
    missing_chunks: list[int]
