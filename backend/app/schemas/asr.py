"""ASR 转写 + 内容护栏契约（F3 语音输入 + B5-c 情绪关怀 / B5b 护栏）"""
from typing import Literal

from pydantic import BaseModel, Field


class AsrTranscribeResponse(BaseModel):
    """转写结果（真实、空白语音与开发 mock 同构）

    - outcome: succeeded / no_speech / mock
    - channel: funasr / sensevoice / local_vad / mock
    - emotion: 声学情绪（开心/难过/生气/惊讶/恐惧/厌恶/平静），B5-c 情绪关怀分层触发依据
    - guardrail: 内容安全审核结论（fail-safe：真实模式下不可用默认拦截）
    """

    text: str = Field(..., description="转写文本")
    outcome: Literal["succeeded", "no_speech", "mock"] = Field(..., description="处理结果")
    channel: Literal["funasr", "sensevoice", "local_vad", "mock"] = Field(
        ..., description="实际使用通道"
    )
    emotion: str = Field("平静", description="声学情绪标签（SenseVoice 通道产出）")
    emotion_confidence: float = Field(
        0.0, ge=0.0, le=1.0, description="声学情绪置信度"
    )
    emotion_source: str = Field("none", description="声学情绪来源")
    emotion_model: str | None = Field(None, description="声学情绪模型")
    confidence: float = Field(0.0, ge=0.0, le=1.0, description="识别置信度")
    duration_ms: int = Field(0, ge=0, description="音频时长（ms）")
    mock: bool = Field(False, description="是否 mock 兜底")
    retryable: bool = Field(False, description="结果是否建议自动重试")
    model: str = Field(..., description="实际模型版本")
    provider_request_id: str | None = Field(None, description="供应商排障请求 ID")
    audio_format: str = Field(..., description="实际音频格式")
    source_audio_sha256: str = Field(..., description="原音频 SHA-256 指纹")
    errors: list[str] = Field(default_factory=list, description="通道降级记录（不含密钥）")
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
