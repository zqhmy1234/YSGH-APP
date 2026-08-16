"""ASR 转写 + 内容护栏契约（F3 语音输入 + B5-c 情绪关怀 / B5b 护栏）"""
from typing import Literal

from pydantic import BaseModel, Field


class AsrTranscribeResponse(BaseModel):
    """转写结果（mock 与真实同构）

    - channel: funasr（语义）/ sensevoice（声学情绪）/ mock（未配 key 兜底）
    - emotion: 声学情绪（开心/难过/生气/惊讶/恐惧/厌恶/平静），B5-c 情绪关怀分层触发依据
    - guardrail: 内容安全审核结论（fail-safe：真实模式下不可用默认拦截）
    """

    text: str = Field(..., description="转写文本")
    channel: Literal["funasr", "sensevoice", "mock"] = Field(..., description="实际使用通道")
    emotion: str = Field("平静", description="声学情绪标签（SenseVoice 通道产出）")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="识别置信度")
    duration_ms: int = Field(0, ge=0, description="音频时长（ms，wav 头解析）")
    mock: bool = Field(False, description="是否 mock 兜底")
    guardrail: "GuardrailVerdict" = Field(..., description="护栏审核结论（B5b）")


class GuardrailVerdict(BaseModel):
    """护栏结论（对齐 dashscope.moderate 输出）"""

    passed: bool = Field(..., description="true=放行；false=拦截（fail-safe 默认拦截）")
    reason: str = Field("", description="拦截原因（放行时为空串）")


class GuardCheckRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000, description="待审核文本（发布/入库前调用）")


class GuardCheckResponse(BaseModel):
    passed: bool
    reason: str = ""


AsrTranscribeResponse.model_rebuild()
