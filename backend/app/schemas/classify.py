"""分类契约（F2 文字碎片 5 类：待办/灵感/情绪/引用/混合）"""
from pydantic import BaseModel, Field


class ClassifyRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000, description="文字碎片内容")
    client_request_id: str | None = Field(
        None, min_length=1, max_length=64,
        description="客户端请求幂等键（R4#4：重复提交返回同一 job，不重复入队）",
    )


class ClassScore(BaseModel):
    label: str
    label_cn: str
    score: float


class ClassifyResult(BaseModel):
    label: str
    label_cn: str
    confidence: float
    scores: list[ClassScore]


class ClassifyJobQueued(BaseModel):
    """分类入队出参（POST /api/v1/classify；P2-01 异步，返回 job_id 供轮询）"""

    job_id: str
    status: str


class ClassifyJobOut(BaseModel):
    """分类任务状态出参（GET /api/v1/classify/jobs/{job_id}）"""

    job_id: str
    status: str
    result: ClassifyResult | None = None
    error: str | None = None


class ArbitrateJobQueued(BaseModel):
    """裁决入队出参（POST /api/v1/classify/arbitrate；P2-01 异步）"""

    job_id: str
    status: str


class ArbitrateJobOut(BaseModel):
    """裁决任务状态出参（GET /api/v1/classify/arbitrate/jobs/{job_id}）"""

    job_id: str
    status: str
    result: dict | None = None
    error: str | None = None
