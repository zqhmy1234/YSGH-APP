"""检索契约（B2 RAG：描述性搜索 + 溯源）"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SearchQuery(BaseModel):
    q: str = Field(..., min_length=1, max_length=200, description="描述性查询（自然语言）")
    intent: Literal["auto", "text", "image"] = "auto"   # auto=LLM 路由（B2 查询路由）
    content_types: list[str] | None = None              # 过滤：photo/text/voice/article
    time_from: datetime | None = None                   # payload filter（非召回路）
    time_to: datetime | None = None
    place: str | None = None
    tag: str | None = None
    limit: int = Field(10, ge=1, le=50)
    cursor: str | None = None


class SearchHit(BaseModel):
    content_id: str
    content_type: str
    text: str | None
    taken_at: datetime | None
    place: str | None
    event_id: str | None
    event_title: str | None
    score: float
    # 溯源（大厂标配 + 记忆类产品信任底线，B2 关键设计约束 #3）
    trace: dict = Field(..., description="命中字段/事件/标签解释，RET-016")


class SearchResult(BaseModel):
    query: str
    rewritten_query: str | None = None      # Query 改写结果（RET-007）
    intent: str                              # text / image / mixed
    hits: list[SearchHit]
    total: int
    latency_ms: int                          # 验收：P95<3s（RET-018）
    degraded: bool = False                   # Qdrant 降级纯 PG 检索标记（API-009）
