"""检索契约（B2 RAG：描述性搜索 + 溯源）"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SearchQuery(BaseModel):
    q: str = Field(..., min_length=1, max_length=200, description="描述性查询（自然语言）")
    # 检索意图（C 方案 2026-11-01 起**真正生效**，此前全仓零使用）：
    #   auto  = 规则路由（含图词且还剩实词 → mixed；纯图指代 → image；否则 text）
    #   text  = 强制全量检索（不做照片过滤、不做双路合并）
    #   image = 强制只回照片（"我只要照片"的窄路，精确性优先，见 rag/intent._resolve_intent）
    intent: Literal["auto", "text", "image"] = "auto"
    content_types: list[str] | None = None              # 过滤：photo/text/voice/article
    time_from: datetime | None = None                   # payload filter（非召回路）
    time_to: datetime | None = None
    place: str | None = None
    tag: str | None = None
    limit: int = Field(10, ge=1, le=50)
    # 2026-11-01（跨模态任务中发现的**分支基线缺口**）：`services/rag/__init__.py`
    # 早在 `_search_impl` 里按 `q.offset` 取数窗口（`window = q.offset + q.limit`），
    # 但本分支的 SearchQuery 缺这个字段 ⇒ 任何一次搜索都会 AttributeError。
    # 补齐契约（offset 分页而非游标：检索结果按分数排序、没有稳定主键可锚）；
    # 不补的话跨模态链路根本跑不起来，也无法用 pytest 断言。
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
    query: str                               # 音频检索时为转写文本（用户可见"被听成了什么"）
    rewritten_query: str | None = None      # Query 改写结果（RET-007；图搜=caption，音搜=转写）
    intent: str                              # text / image / mixed（请求侧 auto/text/image 的对偶）
    hits: list[SearchHit]
    total: int
    # 与 `total` 配套的分页信号（`_search_impl` 一直在传，此前模型缺字段被静默丢弃）：
    # true = 取数窗口被占满，后面可能还有；false = 已到候选池末尾
    has_more: bool = Field(False, description="true = 本次取数窗口被占满，后面可能还有")
    latency_ms: int                          # 验收：P95<3s（RET-018）
    degraded: bool = False                   # Qdrant 降级纯 PG 检索标记（API-009）
