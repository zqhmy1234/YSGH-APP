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
    # 2026-10-30（P2 C-5）：分页。**offset 分页而非游标**——检索结果按向量/重排分数排序，
    # 分数不是稳定主键（同一查询两次召回可能差几名），游标（"上次最后一条"）在语义上
    # 没有锚点；而"跳过前 N 条"在**同一查询串**下是确定的。
    # 代价（如实标注）：每次请求都会重跑召回 + 重排（成本不随翻页摊薄），
    # 且可翻深度受召回窗口限制（见 SearchResult.total 的说明）。
    offset: int = Field(0, ge=0, le=100, description="跳过排序结果的前 N 条（0=第一页）")


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
    # 2026-10-30（P2 C-5）total 的**准确口径**（改造前这里恒等于 len(hits)，即"本页条数"
    # 被当成总数，`offset` 一出现就自相矛盾）：
    #   total = **本次查询可翻页的候选条数** = 召回窗口（上限 `_RECALL_WINDOW`=50）内、
    #   经拒答地板 / 敏感词过滤 / 用户与软删回查后剩下的条数。
    # 它不是"全库匹配条数"：向量检索没有可数的匹配集合，真去全量 count 等于每次查询
    # 扫整个 collection（与本接口 P95<3s 的门禁直接冲突）。
    # 推论（客户端需知）：`offset + limit <= total` 才有下一页；翻到 total 之后返回空数组
    # 而不是报错——「翻到底了」与「一条都没命中」都表现为空 hits，区分靠 total 是否为 0。
    total: int = Field(..., description="可翻页候选条数（≤ 召回窗口 50，非全库匹配数）")
    has_more: bool = Field(
        False,
        description="true = 本次取数窗口被占满，后面**可能**还有（再取会把 offset 加上本页条数）；"
        "false = 已到候选池末尾",
    )
    latency_ms: int                          # 验收：P95<3s（RET-018）
    degraded: bool = False                   # Qdrant 降级纯 PG 检索标记（API-009）
