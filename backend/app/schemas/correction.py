"""纠错契约（B5-c 三层裁决 · F2）"""
from pydantic import BaseModel, Field


class CorrectionCreate(BaseModel):
    content_id: str = Field(..., description="被纠错内容 ID")
    text: str = Field(..., min_length=1, max_length=2000, description="内容文本（用于向量化）")
    new_label: str = Field(..., description="纠正后的分类：todo/idea/emotion/quote/mixed")
    old_label: str | None = Field(None, description="原分类（模型给出的）")
    source: str = Field("active", description="active=主动纠错 / echo=回响确认 / org=整理联动")
    content_type: str = Field("text", description="photo/text/voice")


class CorrectionOut(BaseModel):
    id: int
    content_id: str | None
    old_label: str | None
    new_label: str
    source: str


class ArbitrateRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)
    content_type: str = Field("text", description="photo/text/voice")
    client_request_id: str | None = Field(
        None, min_length=1, max_length=64,
        description="客户端请求幂等键（R4#4：重复提交返回同一 job，不重复入队）",
    )
