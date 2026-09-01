"""ASR 共享数据类型与常量（F7 拆包：跨子模块单一来源，避免循环依赖）。

本模块零依赖（除标准库），供 audio / emotion / backends / transcriber 共同引用。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MODEL_FUNASR = "fun-asr-flash-2026-06-15"
MODEL_SENSEVOICE = "iic/SenseVoiceSmall-onnx"
MODEL_SENSEVOICE_TOKENIZER = "iic/SenseVoiceSmall"
DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
EMOTION_ACTION_THRESHOLD = 0.7


class AsrError(RuntimeError):
    """可安全传给 API/任务状态层的 ASR 错误。"""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        errors: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.errors = list(errors or [])

    @property
    def outcome(self) -> str:
        return "failed_retryable" if self.retryable else "failed_final"

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "errors": self.errors,
        }


@dataclass(frozen=True)
class AudioInfo:
    path: Path
    size_bytes: int
    sha256: str
    audio_format: str
    mime_type: str
    duration_ms: int


@dataclass
class AsrResult:
    """与具体供应商解耦的转写结果。"""

    text: str
    channel: str
    outcome: str = "succeeded"
    emotion: str | None = None  # D-16: 未测得=None（旧「平静」默认值把自己伪装成真读数）
    emotion_confidence: float = 0.0
    emotion_source: str = "none"
    emotion_model: str | None = None
    confidence: float = 0.0
    duration_ms: int = 0
    mock: bool = False
    retryable: bool = False
    model: str = MODEL_FUNASR
    provider: str = "aliyun_model_studio"
    provider_request_id: str | None = None
    audio_format: str = ""
    source_audio_sha256: str = ""
    segments: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    # ---- B5a · Wave4 AgentJ 新增（J-1/J-2/J-3 音频事件/噪音/段级合并）----
    audio_events: list[str] = field(default_factory=list)
    emotion_bonus: bool = False
    silence_hint: bool = False
    not_oral: bool = False
    snr_db: float | None = None
    noise_weight: str = "high"  # high=声学权重大；equal=噪音大时与语义持平
    emotion_merge: dict[str, Any] | None = None  # dominant/peak 段级合并结构

    def audit_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "retryable": self.retryable,
            "channel": self.channel,
            "provider": self.provider,
            "model": self.model,
            "provider_request_id": self.provider_request_id,
            "audio_format": self.audio_format,
            "source_audio_sha256": self.source_audio_sha256,
            "duration_ms": self.duration_ms,
            "emotion": self.emotion,
            "emotion_confidence": self.emotion_confidence,
            "emotion_source": self.emotion_source,
            "emotion_model": self.emotion_model,
            "emotion_actionable": (
                self.emotion is not None
                and self.emotion != "平静"
                and self.emotion_confidence >= EMOTION_ACTION_THRESHOLD
            ),
            "emotion_merge": self.emotion_merge,
            "audio_events": self.audio_events,
            "emotion_bonus": self.emotion_bonus,
            "silence_hint": self.silence_hint,
            "not_oral": self.not_oral,
            "snr_db": self.snr_db,
            "noise_weight": self.noise_weight,
            "confidence": self.confidence,
            "mock": self.mock,
            "segments": self.segments,
            "usage": self.usage,
            "errors": self.errors,
        }


@dataclass(frozen=True)
class SenseVoiceResult:
    """SenseVoice 本地推理结果；置信度只针对声学情绪标签。"""

    text: str
    emotion: str | None  # D-16: EMO_UNKNOWN -> None（未测得）
    emotion_confidence: float
    raw_emotion: str
    audio_events: list[str] = field(default_factory=list)
