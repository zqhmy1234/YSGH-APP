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
    # 异步回答开关（A-11 ③ · 2026-10-24）：默认 **False** ⇒ 行为与今天逐字一致
    # （在野 APK 不知道这个字段）。置 True 时**不再等回答**，响应带 `answer_job_id`，
    # 客户端轮询 `GET /api/v1/search/answers/{job_id}` 取回。
    # ⚠️ 与 `want_answer` 是**且**关系：`want_answer=False` 时本开关无意义（不会有 job）。
    answer_async: bool = False
    # 分阶段打点开关（2026-10-16 检索超时治理 · 立项①）：默认 **False** ⇒ 零开销、
    # 响应与改动前逐字一致；置 True 时 `SearchResult.stages` 返回各阶段毫秒耗时。
    # 仅服务压测/排障用，客户端不感知（在野 APK 零影响）。
    want_stages: bool = False


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
    # ── 异步回答（A-11 ③ · 2026-10-24）──
    # `want_answer=True` **且** `answer_async=True` 时：本次响应**不等回答**，
    # `answer` 恒为 None、本字段给一个 job_id；客户端随后轮询
    # `GET /api/v1/search/answers/{job_id}` 取回（1~2s 内通常就绪）。
    #
    # 为什么要有独立开关而不是直接改 want_answer 语义：
    # 在野 APK 只认 `answer`（它不知道这个字段）⇒ 若直接改成"永远异步"，
    # 老客户端会**永远拿不到回答**。加开关后：老客户端不传 ⇒ 行为与今天逐字一致。
    answer_job_id: str | None = None
    # 分阶段耗时（毫秒，**仅 `want_stages=True` 时非 None**；治理后复测基线用）：
    #   rewrite = 查询改写+过滤归一+路由/类目判定；encode = BGE-M3 dense+sparse 编码；
    #   recall  = 向量召回+空结果回退+精确提升+溯源组装（与 latency_ms 同止点）；
    #   rerank  = bge 粗排+LLM 精排+规则敏感过滤（latency_ms 之后阶段）；
    #   answer  = 搜索式回答（仅 want_answer=True，否则如实 None）。
    # 某阶段未执行/拿不到计时（如编码抛异常走 PG 兜底）⇒ 该键如实 None，**绝不编造**。
    stages: dict[str, int | None] | None = None
