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
    # 搜索式一次性问答（2026-10-16 冻结：搜索=回答卡+证据，AI 页=多轮对话）：
    # 默认 **False** ⇒ 在野 APK 与既有客户端**零影响**（不多一次 LLM 调用、不增延迟）。
    # 客户端置 True 时，结果额外带 `answer`/`answer_sources`。
    want_answer: bool = False


class SearchHit(BaseModel):
    content_id: str
    content_type: str
    text: str | None
    taken_at: datetime | None
    place: str | None
    event_id: str | None
    event_title: str | None
    score: float
    # 命中内容缩略图票据 URL（Valet Key，仅 photo 类型有值；2026-09-04 峰宝拍板搜索结果带图）
    thumbnail_url: str | None = None
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
    # 搜索式一次性回答（仅 `want_answer=True` 时可能非空）：
    #   · 只依据本次召回证据生成，**绝不编造**；证据不足 / 模型不可用 → None；
    #   · 已过 app.services.model_text.strip_model_markup（不残留 DSML/工具调用标记）。
    answer: str | None = None
    # 回答引用的证据 content_id 列表（**必为本次 `hits` 的子集**，供客户端把每条证据挂成卡片）。
    answer_sources: list[str] = []
