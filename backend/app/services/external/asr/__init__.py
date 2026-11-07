"""ASR 统一接入层（F7 拆包）：重导出子包公开接口，外部 import 路径不变。

开发/测试模式可显式使用 mock；真实模式绝不把 mock 当成成功结果。手机常见
M4A/MP3/AAC 等格式由已完成真实验收的 Fun-ASR Flash 通道直接处理，WAV
额外保留 SenseVoice 降级和长录音 VAD 分段能力。

子包职责：
  transcriber.py  转写编排（统一入口 / 通道选择降级 / VAD 分段合并 / 情绪增强）
  backends.py     后端适配（Fun-ASR Flash 百炼 / SenseVoice 本地 / mock）
  audio.py        音频处理/VAD（上传校验 / SNR 估计 / 长录音分段 / 数字静音）
  emotion.py      情绪标签（情绪映射 / 音频事件消费 / 噪音降权 / 段级合并）
  models.py       共享类型与常量（跨子模块单一来源）

说明：下划线前缀的私有辅助（_dashscope_base_url / _call_with_retry / _infer_sensevoice
等）是存量测试 monkeypatch/直调点，重导出保持兼容；用冗余别名标记为有意重导出。
"""
from __future__ import annotations

from .audio import (
    FORMAT_BY_SUFFIX,
    MAX_AUDIO_BYTES,
    MAX_SEG_S,
    MIME_BY_FORMAT,
    MIN_SEG_S,
    NOISE_FLOOR_PERCENTILE,
    SIGNAL_PERCENTILE,
    SILENCE_JOIN_S,
    SNR_FRAME_MS,
    SUFFIX_BY_FORMAT,
    VAD_FRAME_MS,
    estimate_snr,
    inspect_audio,
    temporary_suffix,
    validate_audio_bytes,
)
from .audio import (
    _segments_for as _segments_for,
)
from .backends import (
    _CHANNELS as _CHANNELS,
)
from .backends import (
    _SENSEVOICE_TOKENIZER_NAME as _SENSEVOICE_TOKENIZER_NAME,
)
from .backends import (
    ASR_CHANNELS,
    infer_local_emotion,
    prepare_sensevoice_assets,
)
from .backends import (
    _call_with_retry as _call_with_retry,
)
from .backends import (
    _dashscope_base_url as _dashscope_base_url,
)
from .backends import (
    _http_post_json as _http_post_json,
)
from .backends import (
    _infer_sensevoice as _infer_sensevoice,
)
from .backends import (
    _sensevoice_emotion_confidence as _sensevoice_emotion_confidence,
)
from .backends import (
    _sensevoice_model_dir as _sensevoice_model_dir,
)
from .backends import (
    _transcribe_sensevoice as _transcribe_sensevoice,
)
from .emotion import (
    AUDIO_EVENT_ENVIRONMENT,
    AUDIO_EVENT_LAUGHTER,
    AUDIO_EVENT_NONE,
    AUDIO_EVENT_SILENCE,
    NOISE_SNR_THRESHOLD_DB,
    SENSEVOICE_EMOTION_TAGS,
    SENSEVOICE_UNKNOWN_EMOTION_TAG,
    apply_audio_event_effects,
    merge_segment_emotion,
    single_segment_emotion_merge,
)
from .emotion import (
    _noise_weight as _noise_weight,
)
from .emotion import (
    _parse_audio_events as _parse_audio_events,
)
from .models import (
    DEFAULT_DASHSCOPE_BASE_URL,
    EMOTION_ACTION_THRESHOLD,
    MODEL_FUNASR,
    MODEL_SENSEVOICE,
    MODEL_SENSEVOICE_TOKENIZER,
    AsrError,
    AsrResult,
    AudioInfo,
    SenseVoiceResult,
)
from .transcriber import (
    _enhance_with_local_emotion as _enhance_with_local_emotion,
)
from .transcriber import (
    should_enhance_with_local_emotion,
    transcribe,
)

__all__ = [
    "MODEL_FUNASR",
    "MODEL_SENSEVOICE",
    "MODEL_SENSEVOICE_TOKENIZER",
    "DEFAULT_DASHSCOPE_BASE_URL",
    "MAX_AUDIO_BYTES",
    "EMOTION_ACTION_THRESHOLD",
    "FORMAT_BY_SUFFIX",
    "SUFFIX_BY_FORMAT",
    "MIME_BY_FORMAT",
    "SENSEVOICE_EMOTION_TAGS",
    "SENSEVOICE_UNKNOWN_EMOTION_TAG",
    "ASR_CHANNELS",
    "AUDIO_EVENT_LAUGHTER",
    "AUDIO_EVENT_SILENCE",
    "AUDIO_EVENT_ENVIRONMENT",
    "AUDIO_EVENT_NONE",
    "NOISE_SNR_THRESHOLD_DB",
    "NOISE_FLOOR_PERCENTILE",
    "SIGNAL_PERCENTILE",
    "SNR_FRAME_MS",
    "VAD_FRAME_MS",
    "MIN_SEG_S",
    "MAX_SEG_S",
    "SILENCE_JOIN_S",
    "AsrError",
    "AudioInfo",
    "AsrResult",
    "SenseVoiceResult",
    "validate_audio_bytes",
    "temporary_suffix",
    "inspect_audio",
    "estimate_snr",
    "prepare_sensevoice_assets",
    "infer_local_emotion",
    "apply_audio_event_effects",
    "merge_segment_emotion",
    "single_segment_emotion_merge",
    "should_enhance_with_local_emotion",
    "transcribe",
]
