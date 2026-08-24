"""事件契约（B3 四层事件模型：L0 瞬间 / L1 日 / L2 主题 / L3 流）"""
from datetime import datetime

from pydantic import BaseModel, Field


class EventOut(BaseModel):
    id: str
    level: int = Field(..., ge=0, le=3)
    title: str | None
    title_source: str | None            # llm / template / user
    cover_content_id: str | None
    start_time: datetime | None
    end_time: datetime | None
    place: str | None
    tags: list[str] = []
    emotion: dict | None
    sensitivity: str | None
    confidence: float | None
    status: str                         # draft / confirmed / rejected
    generated_by: str                   # device / cloud
    content_count: int = 0
    photo_count: int = 0


class EventMergeRequest(BaseModel):
    """用户手动合并事件（B3-5：存合并规则，用户操作优先）"""

    target_event_id: str
    source_event_ids: list[str] = Field(..., min_length=1)


class EventSplitRequest(BaseModel):
    """用户手动拆分事件"""

    event_id: str
    content_ids: list[str] = Field(..., min_length=1)


class EventConfirmRequest(BaseModel):
    """用户确认事件（置信度<0.7 转正；用户背书后算法不再改动）"""

    event_id: str
    title: str | None = None


class ClientEventItem(BaseModel):
    """端侧 L1 事件（S-SY-1：client_event_id 幂等键）

    端侧 ST-DBSCAN（B3-6 端侧 L0/L1 真值）产出的日卡片事件；
    云侧只做归属校验 + 落库 + L2/L3 候选（caption/CI 打标已在 _process_photo）。
    """

    client_event_id: str = Field(
        ..., min_length=1, max_length=64, description="客户端生成事件 ID（幂等键，同用户唯一）"
    )
    title: str | None = Field(None, max_length=100, description="模板标题（空则服务端按日期生成）")
    start_time: datetime = Field(..., description="事件窗起点（ISO8601）")
    end_time: datetime | None = None
    place: str | None = Field(None, max_length=100)
    photo_ids: list[str] = Field(
        default_factory=list, min_length=1, max_length=500, description="成员照片 content_id 列表"
    )


class EventSyncRequest(BaseModel):
    """端侧事件批量提交（S-SY-1）"""

    device_id: str = Field(..., min_length=1, max_length=64)
    events: list[ClientEventItem] = Field(..., min_length=1, max_length=100)


class EventSyncResult(BaseModel):
    """端侧事件提交结果（幂等 + 越权拒绝明细）"""

    accepted: list[dict] = []      # [{client_event_id, event_id, photo_count}]
    duplicates: list[str] = []     # 已存在的 client_event_id（网络重试幂等命中）
    rejected: list[dict] = []      # [{client_event_id, reason}]（越权/非法被拒）
    upper_items: int = 0           # 云侧 L2/L3 候选新增成员数
