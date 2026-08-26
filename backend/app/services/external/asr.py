"""ASR 统一接入层（Fun-ASR Flash + SenseVoice + 本地 VAD）。

开发/测试模式可显式使用 mock；真实模式绝不把 mock 当成成功结果。手机常见
M4A/MP3/AAC 等格式由已完成真实验收的 Fun-ASR Flash 通道直接处理，WAV
额外保留 SenseVoice 降级和长录音 VAD 分段能力。
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
import wave
from array import array
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib import error, request

from app.core.config import settings

logger = logging.getLogger("yishu.asr")

MODEL_FUNASR = "fun-asr-flash-2026-06-15"
MODEL_SENSEVOICE = "iic/SenseVoiceSmall-onnx"
MODEL_SENSEVOICE_TOKENIZER = "iic/SenseVoiceSmall"
DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/api/v1"
MAX_AUDIO_BYTES = 8 * 1024 * 1024
EMOTION_ACTION_THRESHOLD = 0.7

FORMAT_BY_SUFFIX = {
    ".aac": "aac",
    ".amr": "amr",
    ".flac": "flac",
    ".m4a": "m4a",
    ".mp3": "mp3",
    ".ogg": "ogg",
    ".opus": "opus",
    ".wav": "wav",
    ".webm": "webm",
    ".wma": "wma",
}
SUFFIX_BY_FORMAT = {value: key for key, value in FORMAT_BY_SUFFIX.items()}
MIME_BY_FORMAT = {
    "aac": "audio/aac",
    "amr": "audio/amr",
    "flac": "audio/flac",
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "opus": "audio/opus",
    "wav": "audio/wav",
    "webm": "audio/webm",
    "wma": "audio/x-ms-wma",
}

SENSEVOICE_EMOTION_TAGS = {
    "<|HAPPY|>": "开心",
    "<|SAD|>": "难过",
    "<|ANGRY|>": "生气",
    "<|NEUTRAL|>": "平静",
    "<|FEARFUL|>": "恐惧",
    "<|DISGUSTED|>": "厌恶",
    "<|SURPRISED|>": "惊讶",
}
SENSEVOICE_UNKNOWN_EMOTION_TAG = "<|EMO_UNKNOWN|>"
ASR_CHANNELS = ("funasr", "sensevoice")

# 音频事件（B5a · Wave4 AgentJ：SenseVoice 12 类取 3 消费，其余 9 类 MVP 不消费）
AUDIO_EVENT_LAUGHTER = "laughter"        # 笑声 → 情绪加分
AUDIO_EVENT_SILENCE = "silence"          # 静音 → 提示空段
AUDIO_EVENT_ENVIRONMENT = "environment"  # 键盘/环境音 → 疑似非口述
AUDIO_EVENT_NONE = "none"
# SenseVoice 富文本事件标签 → 消费类目（None = 不消费）
_SENSEVOICE_AUDIO_EVENT_TAGS = {
    "laughter": AUDIO_EVENT_LAUGHTER,
    "giggle": AUDIO_EVENT_LAUGHTER,
    "chuckle": AUDIO_EVENT_LAUGHTER,
    "silence": AUDIO_EVENT_SILENCE,
    "bgm": AUDIO_EVENT_ENVIRONMENT,
    "music": AUDIO_EVENT_ENVIRONMENT,
    "applause": AUDIO_EVENT_ENVIRONMENT,
    "noise": AUDIO_EVENT_ENVIRONMENT,
    "keyboard": AUDIO_EVENT_ENVIRONMENT,
    "typing": AUDIO_EVENT_ENVIRONMENT,
    "speech": None,
    "breath": None,
    "cough": None,
    "sneeze": None,
    "scream": None,
}

# 噪音降权（J-2）：轻量 SNR 检测；低于阈值 → 声学情绪权重降为与语义持平
NOISE_SNR_THRESHOLD_DB = 15.0
NOISE_FLOOR_PERCENTILE = 10.0
SIGNAL_PERCENTILE = 90.0
SNR_FRAME_MS = 20

_sensevoice_state: dict[str, Any | None] = {"model": None}
_sensevoice_model_lock = threading.Lock()


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
    emotion: str = "平静"
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
                self.emotion != "平静"
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
    emotion: str
    emotion_confidence: float
    raw_emotion: str
    audio_events: list[str] = field(default_factory=list)


def _matches_magic(audio_format: str, data: bytes) -> bool:
    if audio_format == "wav":
        return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WAVE"
    if audio_format == "m4a":
        return len(data) >= 12 and data[4:8] == b"ftyp"
    if audio_format == "mp3":
        return data.startswith(b"ID3") or (
            len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0
        )
    if audio_format == "aac":
        return len(data) >= 2 and data[0] == 0xFF and data[1] & 0xF6 == 0xF0
    if audio_format == "flac":
        return data.startswith(b"fLaC")
    if audio_format in {"ogg", "opus"}:
        return data.startswith(b"OggS")
    if audio_format == "amr":
        return data.startswith((b"#!AMR\n", b"#!AMR-WB\n"))
    if audio_format == "webm":
        return data.startswith(b"\x1aE\xdf\xa3")
    if audio_format == "wma":
        return data.startswith(b"0&\xb2u\x8ef\xcf\x11\xa6\xd9\x00\xaa\x00b\xcel")
    return False


def validate_audio_bytes(
    data: bytes,
    filename: str | None = None,
    *,
    max_bytes: int | None = MAX_AUDIO_BYTES,
) -> str:
    """校验上传音频并返回供应商格式名。"""
    if not data:
        raise AsrError("EMPTY_AUDIO", "音频文件为空")
    if max_bytes is not None and len(data) > max_bytes:
        max_mb = max_bytes / (1024 * 1024)
        raise AsrError("AUDIO_TOO_LARGE", f"音频超过 {max_mb:g}MB 上限")

    suffix = Path(filename or "").suffix.lower()
    audio_format = FORMAT_BY_SUFFIX.get(suffix)
    if not audio_format:
        supported = ", ".join(sorted(FORMAT_BY_SUFFIX))
        raise AsrError(
            "UNSUPPORTED_FORMAT",
            f"不支持的音频格式 {suffix or '无扩展名'}；支持：{supported}",
        )
    if not _matches_magic(audio_format, data[:64]):
        raise AsrError("INVALID_AUDIO", "文件内容与音频扩展名不一致或文件已损坏")
    return audio_format


def temporary_suffix(audio_format: str) -> str:
    return SUFFIX_BY_FORMAT.get(audio_format, ".bin")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_wav_duration(path: Path) -> int:
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 1
            return int(frames / rate * 1000)
    except Exception:  # noqa: BLE001
        return 0


def inspect_audio(path: str | Path, *, max_bytes: int | None = None) -> AudioInfo:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise AsrError("AUDIO_NOT_FOUND", f"音频文件不存在: {resolved}")
    data = resolved.read_bytes()
    audio_format = validate_audio_bytes(data, resolved.name, max_bytes=max_bytes)
    return AudioInfo(
        path=resolved,
        size_bytes=len(data),
        sha256=_sha256_file(resolved),
        audio_format=audio_format,
        mime_type=MIME_BY_FORMAT[audio_format],
        duration_ms=_parse_wav_duration(resolved) if audio_format == "wav" else 0,
    )


def _dashscope_base_url() -> str:
    explicit = (
        os.getenv("DASHSCOPE_BASE_URL", "").strip()
        or settings.dashscope_base_url.strip()
    )
    if explicit:
        return explicit.rstrip("/")
    workspace_id = settings.dashscope_workspace_id.strip()
    if workspace_id:
        return (
            f"https://{workspace_id}.{settings.dashscope_region}.maas.aliyuncs.com/api/v1"
        )
    return DEFAULT_DASHSCOPE_BASE_URL


def _build_flash_payload(audio: AudioInfo) -> dict[str, Any]:
    encoded = base64.b64encode(audio.path.read_bytes()).decode("ascii")
    return {
        "model": MODEL_FUNASR,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": f"data:{audio.mime_type};base64,{encoded}",
                            },
                        }
                    ],
                }
            ]
        },
        "parameters": {"format": audio.audio_format, "language_hints": ["zh"]},
    }


def _http_post_json(
    url: str,
    api_key: str,
    payload: dict[str, Any],
    timeout_seconds: float = 180.0,
) -> tuple[int, dict[str, Any]]:
    if not url.startswith("https://"):
        raise AsrError("INVALID_ENDPOINT", "ASR 服务地址必须使用 HTTPS")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(  # noqa: S310 -- URL 已在上方限制为 HTTPS
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "X-DashScope-SSE": "disable",
        },
    )
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw)
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            detail = json.loads(raw)
            message = detail.get("message") or detail.get("code") or raw
        except json.JSONDecodeError:
            message = raw
        retryable = exc.code == 429 or 500 <= exc.code <= 599
        raise AsrError(
            f"HTTP_{exc.code}",
            str(message)[:500],
            retryable=retryable,
        ) from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise AsrError("NETWORK_ERROR", f"ASR 网络请求失败: {exc}", retryable=True) from exc
    except json.JSONDecodeError as exc:
        raise AsrError("INVALID_PROVIDER_JSON", "ASR 返回了无法解析的数据", retryable=True) from exc


def _call_with_retry(
    call: Callable[[], tuple[int, dict[str, Any]]],
    retries: int = 2,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[int, dict[str, Any]]:
    attempt = 0
    while True:
        try:
            return call()
        except AsrError as exc:
            if not exc.retryable or attempt >= retries:
                raise
            sleep(min(2**attempt, 8))
            attempt += 1


def _normalise_segments(output: dict[str, Any]) -> list[dict[str, Any]]:
    sentence = output.get("sentence")
    if isinstance(sentence, dict):
        return [sentence]
    if isinstance(sentence, list):
        return [item for item in sentence if isinstance(item, dict)]
    return []


def _transcribe_funasr(path: Path) -> AsrResult:
    """主通道：Fun-ASR Flash，多格式 Data URI 调用。"""
    audio = inspect_audio(path)
    endpoint = f"{_dashscope_base_url()}/services/aigc/multimodal-generation/generation"
    _, response = _call_with_retry(
        lambda: _http_post_json(
            endpoint,
            settings.dashscope_api_key,
            _build_flash_payload(audio),
        )
    )
    output = response.get("output")
    if not isinstance(output, dict) or not isinstance(output.get("text"), str):
        raise AsrError(
            "INVALID_PROVIDER_RESPONSE",
            "ASR 响应缺少 output.text",
            retryable=True,
        )
    text = output["text"].strip()
    segments = _normalise_segments(output)
    confidence = 0.0
    if segments and isinstance(segments[0].get("confidence"), (int, float)):
        confidence = float(segments[0]["confidence"])
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    duration_ms = audio.duration_ms
    if not duration_ms and isinstance(usage.get("duration"), (int, float)):
        duration_ms = int(float(usage["duration"]) * 1000)
    return AsrResult(
        text=text,
        channel="funasr",
        outcome="succeeded" if text else "no_speech",
        confidence=confidence,
        duration_ms=duration_ms,
        model=MODEL_FUNASR,
        provider_request_id=response.get("request_id"),
        audio_format=audio.audio_format,
        source_audio_sha256=audio.sha256,
        segments=segments,
        usage=usage,
    )


# ---- B5a · Wave4 AgentJ：音频事件 / 噪音降权 / 段级情绪合并 ----

def _parse_audio_events(raw_text: str) -> list[str]:
    """从 SenseVoice 富文本标签解析音频事件（12 类取 3 消费，其余忽略）。"""
    events: list[str] = []
    for tag in re.findall(r"<\|([^|]+)\|>", raw_text):
        event = _SENSEVOICE_AUDIO_EVENT_TAGS.get(tag.strip().lower())
        if event and event not in events:
            events.append(event)
    return events


def apply_audio_event_effects(result: AsrResult) -> None:
    """消费音频事件 3 类：笑声→情绪加分；静音→提示空段；键盘/环境音→疑似非口述。"""
    events = set(result.audio_events)
    if AUDIO_EVENT_LAUGHTER in events:
        # 笑声 = 正向情绪信号（B5a §2 情绪加分）：中性/低置信时提为"开心"，
        # 不覆盖已存在的强负向情绪（哭着笑由语义侧兜底）。
        result.emotion_bonus = True
        if result.emotion == "平静" or result.emotion_confidence < EMOTION_ACTION_THRESHOLD:
            if result.emotion == "平静":
                result.emotion = "开心"
                result.emotion_confidence = max(result.emotion_confidence, 0.6)
                if result.emotion_source in {"", "none"}:
                    result.emotion_source = "audio_event_laughter"
    if AUDIO_EVENT_SILENCE in events:
        result.silence_hint = True
    if AUDIO_EVENT_ENVIRONMENT in events:
        result.not_oral = True


def estimate_snr(path: str | Path) -> float | None:
    """轻量信噪比估计（仅 16bit 单声道 WAV）：分帧能量 → 底噪=低分位/信号=高分位。

    返回 dB 值；非 16bit 单声道 WAV 或读取失败返回 None（不参与降权）。
    """
    import numpy as np

    try:
        with wave.open(str(path), "rb") as wf:
            if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
                return None
            rate = wf.getframerate() or 16000
            pcm = wf.readframes(wf.getnframes())
    except (OSError, EOFError, wave.Error):
        return None
    if not pcm:
        return None
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    if samples.size == 0:
        return None
    frame_len = max(1, int(rate * SNR_FRAME_MS / 1000))
    usable = samples.size - samples.size % frame_len
    if usable < frame_len:
        usable = samples.size
        frame_len = samples.size
    frames = samples[:usable].reshape(-1, frame_len)
    energies = (frames * frames).mean(axis=1)
    if energies.size == 0 or float(energies.max()) <= 0.0:
        return None
    noise = float(np.percentile(energies, NOISE_FLOOR_PERCENTILE))
    signal = float(np.percentile(energies, SIGNAL_PERCENTILE))
    noise = max(noise, 1e-12)
    snr_db = 10.0 * math.log10(max(signal, noise) / noise)
    return round(float(snr_db), 1)


def _noise_weight(snr_db: float | None) -> str:
    """噪音大（SNR 低于阈值）→ 声学情绪权重降为与语义持平（B5a §2 输入分布兜底）。"""
    if snr_db is None or snr_db >= NOISE_SNR_THRESHOLD_DB:
        return "high"
    return "equal"


def merge_segment_emotion(
    results: list[AsrResult],
    *,
    noise_weight: str = "high",
) -> dict[str, Any] | None:
    """段级情绪合并（J-3，对齐 B5a §3 设计）：主导 = 时长最长段；峰值保留为标记。

    输出结构：
      dominant: {emotion, confidence, segment_index, duration_ms}   主导段（时长最长）
      peak:     {emotion, confidence, segment_index}                峰值段（置信度最高）
      segments: 每段 {index, emotion, confidence, duration_ms}
      strategy: longest_dominant_peak | single_segment
      noise_weight: high | equal（J-2 噪音降权登记）
    """
    if not results:
        return None
    metas = []
    for index, item in enumerate(results, 1):
        metas.append(
            {
                "index": index,
                "emotion": item.emotion,
                "confidence": item.emotion_confidence,
                "duration_ms": item.duration_ms,
            }
        )
    dominant = max(metas, key=lambda m: m["duration_ms"])
    peak = max(metas, key=lambda m: m["confidence"])
    return {
        "dominant": {
            "emotion": dominant["emotion"],
            "confidence": dominant["confidence"],
            "segment_index": dominant["index"],
            "duration_ms": dominant["duration_ms"],
        },
        "peak": {
            "emotion": peak["emotion"],
            "confidence": peak["confidence"],
            "segment_index": peak["index"],
        },
        "segments": metas,
        "strategy": "longest_dominant_peak",
        "noise_weight": noise_weight,
    }


def single_segment_emotion_merge(result: AsrResult, *, noise_weight: str) -> dict[str, Any]:
    """非分段路径的合并结构（单段：dominant == peak == 自身）。"""
    return {
        "dominant": {
            "emotion": result.emotion,
            "confidence": result.emotion_confidence,
            "segment_index": 1,
            "duration_ms": result.duration_ms,
        },
        "peak": {
            "emotion": result.emotion,
            "confidence": result.emotion_confidence,
            "segment_index": 1,
        },
        "segments": [
            {
                "index": 1,
                "emotion": result.emotion,
                "confidence": result.emotion_confidence,
                "duration_ms": result.duration_ms,
            }
        ],
        "strategy": "single_segment",
        "noise_weight": noise_weight,
    }


_SENSEVOICE_TOKENIZER_NAME = "chn_jpn_yue_eng_ko_spectok.bpe.model"


def _validate_sensevoice_assets(model_dir: Path) -> Path:
    """确认部署目录包含 ONNX 权重和 funasr-onnx 所需分词文件。"""
    resolved = model_dir.expanduser().resolve()
    if not resolved.is_dir() or not any(resolved.glob("*.onnx")):
        raise AsrError(
            "SENSEVOICE_MODEL_NOT_PRELOADED",
            f"SenseVoice 目录缺少 ONNX 权重: {resolved}",
        )
    if not (resolved / _SENSEVOICE_TOKENIZER_NAME).is_file():
        raise AsrError(
            "SENSEVOICE_MODEL_NOT_PRELOADED",
            f"SenseVoice 目录缺少分词文件: {resolved}",
        )
    return resolved


def prepare_sensevoice_assets(
    target_dir: str | Path | None = None,
    *,
    snapshot_download_fn: Callable[..., str] | None = None,
) -> Path:
    """部署/开发预置 SenseVoice 资产；生产请求路径不负责联网下载。"""
    if snapshot_download_fn is None:
        from modelscope import snapshot_download as snapshot_download_fn

    target = Path(target_dir).expanduser().resolve() if target_dir else None
    download_kwargs: dict[str, Any] = {}
    if target is not None:
        target.mkdir(parents=True, exist_ok=True)
        download_kwargs["local_dir"] = str(target)

    model_dir = Path(snapshot_download_fn(MODEL_SENSEVOICE, **download_kwargs))
    tokenizer_path = model_dir / _SENSEVOICE_TOKENIZER_NAME
    if not tokenizer_path.exists():
        tokenizer_dir = Path(
            snapshot_download_fn(
                MODEL_SENSEVOICE_TOKENIZER,
                allow_patterns=[_SENSEVOICE_TOKENIZER_NAME],
            )
        )
        shutil.copy2(tokenizer_dir / _SENSEVOICE_TOKENIZER_NAME, tokenizer_path)
    return _validate_sensevoice_assets(model_dir)


def _sensevoice_model_dir() -> Path:
    configured = settings.sensevoice_model_dir.strip()
    if configured:
        model_dir = Path(configured).expanduser()
        if not model_dir.is_absolute():
            model_dir = Path(__file__).resolve().parents[3] / model_dir
        return _validate_sensevoice_assets(model_dir)
    if settings.app_env == "production":
        raise AsrError(
            "SENSEVOICE_MODEL_NOT_PRELOADED",
            "生产环境必须先运行 scripts/prepare_sensevoice.py 并配置 SENSEVOICE_MODEL_DIR",
        )
    return prepare_sensevoice_assets()


def _get_sensevoice_model():
    """加载已预置的量化 ONNX 模型；开发环境仍允许使用 ModelScope 缓存。"""
    if _sensevoice_state["model"] is not None:
        return _sensevoice_state["model"]

    with _sensevoice_model_lock:
        if _sensevoice_state["model"] is not None:
            return _sensevoice_state["model"]

        from funasr_onnx import SenseVoiceSmall

        _sensevoice_state["model"] = SenseVoiceSmall(
            _sensevoice_model_dir(),
            batch_size=1,
            quantize=True,
            intra_op_num_threads=4,
        )
        return _sensevoice_state["model"]


def _decode_audio_mono_16k(path: Path):
    """使用随应用安装的 FFmpeg，把常见手机音频统一解码为 float32 PCM。"""
    import numpy as np
    from imageio_ffmpeg import get_ffmpeg_exe

    try:
        completed = subprocess.run(  # noqa: S603 -- 参数列表固定且不经过 shell
            [
                get_ffmpeg_exe(),
                "-nostdin",
                "-loglevel",
                "error",
                "-i",
                str(path),
                "-f",
                "f32le",
                "-acodec",
                "pcm_f32le",
                "-ac",
                "1",
                "-ar",
                "16000",
                "pipe:1",
            ],
            capture_output=True,
            check=False,
            timeout=90,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise AsrError(
            "AUDIO_DECODE_FAILED",
            f"本地情绪检测无法解码音频: {type(exc).__name__}",
        ) from exc
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise AsrError(
            "AUDIO_DECODE_FAILED",
            f"本地情绪检测无法解码音频: {detail[-300:]}",
        )
    waveform = np.frombuffer(completed.stdout, dtype="<f4").copy()
    if waveform.size == 0:
        raise AsrError("NO_SPEECH", "本地情绪检测未读取到有效声音")
    return waveform


def _decode_sensevoice_ctc(model, logits, length: int) -> str:
    import numpy as np

    sequence = np.argmax(logits[:length], axis=-1)
    if sequence.size:
        sequence = sequence[np.concatenate(([True], np.diff(sequence) != 0))]
    token_ids = sequence[sequence != model.blank_id].tolist()
    return model.tokenizer.decode(token_ids)


def _sensevoice_emotion_confidence(model, logits, raw_emotion: str) -> float:
    """从第二个富转写查询位计算 7 类情绪的归一化置信度。"""
    import numpy as np

    if raw_emotion not in SENSEVOICE_EMOTION_TAGS or logits.shape[0] < 2:
        return 0.0
    tags = [*SENSEVOICE_EMOTION_TAGS, SENSEVOICE_UNKNOWN_EMOTION_TAG]
    token_ids = [model.tokenizer.sp.PieceToId(tag) for tag in tags]
    if any(token_id < 0 for token_id in token_ids):
        return 0.0
    scores = np.asarray(logits[1, token_ids], dtype=np.float64)
    scores -= float(scores.max())
    probabilities = np.exp(scores)
    denominator = float(probabilities.sum())
    if not math.isfinite(denominator) or denominator <= 0:
        return 0.0
    confidence = float(probabilities[tags.index(raw_emotion)] / denominator)
    return max(0.0, min(confidence, 1.0))


def _infer_sensevoice(path: Path) -> SenseVoiceResult:
    """在 CPU 上运行 SenseVoiceSmall，一次得到本地文本和声学情绪。"""
    import numpy as np

    inspect_audio(path)
    waveform = _decode_audio_mono_16k(path)
    model = _get_sensevoice_model()
    try:
        features, feature_lengths = model.extract_feat([waveform])
        language, textnorm = model.read_tags("auto", "withitn")
        logits, output_lengths = model.infer(
            features,
            feature_lengths,
            np.asarray(language, dtype=np.int32),
            np.asarray(textnorm, dtype=np.int32),
        )
        length = int(output_lengths[0])
        sample_logits = logits[0, :length, :]
        raw_text = _decode_sensevoice_ctc(model, sample_logits, length)
    except Exception as exc:  # noqa: BLE001 -- 三方模型错误统一成安全错误码
        raise AsrError(
            "SENSEVOICE_INFERENCE_FAILED",
            f"本地情绪检测失败: {type(exc).__name__}",
        ) from exc

    raw_emotion = next(
        (tag for tag in SENSEVOICE_EMOTION_TAGS if tag in raw_text),
        SENSEVOICE_UNKNOWN_EMOTION_TAG,
    )
    clean_text = re.sub(r"<\|[^|]+\|>", "", raw_text).strip()
    return SenseVoiceResult(
        text=clean_text,
        emotion=SENSEVOICE_EMOTION_TAGS.get(raw_emotion, "平静"),
        emotion_confidence=_sensevoice_emotion_confidence(
            model,
            sample_logits,
            raw_emotion,
        ),
        raw_emotion=raw_emotion,
        audio_events=_parse_audio_events(raw_text),
    )


def infer_local_emotion(audio_path: str | Path) -> SenseVoiceResult:
    """供独立 RQ 情绪任务调用的稳定入口。"""
    return _infer_sensevoice(Path(audio_path))


def _transcribe_sensevoice(path: Path) -> AsrResult:
    """本地 CPU 降级通道：SenseVoiceSmall 转写 + 声学情绪。"""
    audio = inspect_audio(path)
    local = _infer_sensevoice(path)
    result = AsrResult(
        text=local.text,
        channel="sensevoice",
        outcome="succeeded" if local.text else "no_speech",
        emotion=local.emotion,
        emotion_confidence=local.emotion_confidence,
        emotion_source="sensevoice_local",
        emotion_model=MODEL_SENSEVOICE,
        duration_ms=audio.duration_ms,
        model=MODEL_SENSEVOICE,
        provider="local",
        audio_format=audio.audio_format,
        source_audio_sha256=audio.sha256,
        audio_events=list(local.audio_events),
    )
    if result.outcome == "succeeded":
        apply_audio_event_effects(result)
    return result


def should_enhance_with_local_emotion(
    result: AsrResult,
    *,
    mode: str | None = None,
) -> bool:
    """按策略决定是否需要本地情绪；auto 会尊重未来主通道的情绪结果。"""
    selected = mode or settings.asr_local_emotion_mode
    if result.outcome != "succeeded" or not result.text.strip():
        return False
    if selected == "off":
        return False
    if selected == "always":
        return True
    return result.emotion_source in {"", "none"}


def _enhance_with_local_emotion(
    result: AsrResult,
    path: Path,
    *,
    mode: str | None = None,
) -> AsrResult:
    """云端转写成功后可选本地情绪；情绪失败不抹掉真实文本。"""
    if not should_enhance_with_local_emotion(result, mode=mode):
        return result
    try:
        local = _infer_sensevoice(path)
    except AsrError as exc:
        result.errors.append(f"sensevoice_emotion:{exc.code}")
        logger.warning("SenseVoice 情绪增强失败: %s", exc.code)
        return result
    except Exception as exc:  # noqa: BLE001
        result.errors.append("sensevoice_emotion:INFERENCE_ERROR")
        logger.warning("SenseVoice 情绪增强异常: %s", type(exc).__name__)
        return result
    result.emotion = local.emotion
    result.emotion_confidence = local.emotion_confidence
    result.emotion_source = "sensevoice_local"
    result.emotion_model = MODEL_SENSEVOICE
    result.audio_events = list(local.audio_events)
    apply_audio_event_effects(result)
    return result


def _transcribe_mock(path: Path, errors: list[str] | None = None) -> AsrResult:
    audio = inspect_audio(path)
    return AsrResult(
        text="这是一段本地模拟转写文本。",
        channel="mock",
        outcome="mock",
        emotion="平静",
        confidence=0.5,
        duration_ms=audio.duration_ms,
        mock=True,
        model="mock",
        provider="local",
        audio_format=audio.audio_format,
        source_audio_sha256=audio.sha256,
        errors=list(errors or ["mock mode enabled"]),
    )


def _wav_is_digital_silence(path: Path) -> bool:
    """识别全零/近全零 PCM；不把普通低音量录音误判为空白。"""
    try:
        with wave.open(str(path), "rb") as wf:
            if wf.getsampwidth() != 2 or wf.getnchannels() != 1:
                return False
            saw_sample = False
            while True:
                chunk = wf.readframes(4096)
                if not chunk:
                    break
                samples = array("h")
                samples.frombytes(chunk)
                saw_sample = saw_sample or bool(samples)
                if any(abs(sample) > 4 for sample in samples):
                    return False
            return saw_sample
    except (OSError, EOFError, wave.Error):
        return False


def _no_speech_result(audio: AudioInfo) -> AsrResult:
    return AsrResult(
        text="",
        channel="local_vad",
        outcome="no_speech",
        model="digital-silence-v1",
        provider="local",
        duration_ms=audio.duration_ms,
        audio_format=audio.audio_format,
        source_audio_sha256=audio.sha256,
    )


_CHANNELS = {
    "funasr": _transcribe_funasr,
    "sensevoice": _transcribe_sensevoice,
}


def _transcribe_one(
    path: Path,
    preferred: str,
    errors: list[str],
    *,
    enhance_emotion: bool | None = None,
) -> AsrResult:
    if preferred == "mock":
        if settings.app_env == "production":
            raise AsrError("MOCK_DISABLED", "生产环境禁止使用 mock 转写")
        return _transcribe_mock(path, errors)
    if settings.mock_external_ai:
        if settings.app_env == "production":
            raise AsrError("MOCK_DISABLED", "生产环境禁止使用 mock 转写")
        return _transcribe_mock(path, errors)
    if not settings.dashscope_api_key:
        raise AsrError("MISSING_API_KEY", "ASR 服务未配置 DASHSCOPE_API_KEY")

    order = list(ASR_CHANNELS) if preferred == "auto" else [preferred] + [
        item for item in ASR_CHANNELS if item != preferred
    ]
    retryable = False
    for name in order:
        try:
            result = _CHANNELS[name](path)
            result.errors = list(errors)
            if name == "funasr":
                mode = (
                    "always"
                    if enhance_emotion is True
                    else "off"
                    if enhance_emotion is False
                    else None
                )
                result = _enhance_with_local_emotion(result, path, mode=mode)
            return result
        except AsrError as exc:
            retryable = retryable or exc.retryable
            errors.append(f"{name}:{exc.code}")
            logger.warning("ASR 通道 %s 失败，降级: %s", name, exc.code)
        except Exception as exc:  # noqa: BLE001
            retryable = True
            errors.append(f"{name}:PROVIDER_ERROR")
            logger.warning("ASR 通道 %s 异常，降级: %s", name, type(exc).__name__)
    raise AsrError(
        "ASR_UNAVAILABLE",
        "语音转写暂不可用",
        retryable=retryable,
        errors=errors,
    )


# ---- 长录音 VAD 分段（仅 WAV 16bit 单声道）----
VAD_FRAME_MS = 10
MIN_SEG_S = 120
MAX_SEG_S = 240
SILENCE_JOIN_S = 1.5


def _vad_frame_marks(pcm: bytes, rate: int) -> list[bool]:
    import webrtcvad

    vad = webrtcvad.Vad(2)
    frame_bytes = int(rate * VAD_FRAME_MS / 1000) * 2
    marks: list[bool] = []
    for index in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
        try:
            marks.append(vad.is_speech(pcm[index : index + frame_bytes], rate))
        except Exception:  # noqa: BLE001
            marks.append(False)
    return marks


def _split_marks_to_segments(marks: list[bool]) -> list[tuple[int, int]]:
    if not marks or not any(marks):
        return []
    join_frames = int(SILENCE_JOIN_S * 1000 / VAD_FRAME_MS)
    max_frames = int(MAX_SEG_S * 1000 / VAD_FRAME_MS)
    min_frames = int(MIN_SEG_S * 1000 / VAD_FRAME_MS)
    spans: list[tuple[int, int]] = []
    cur_start: int | None = None
    silence_run = 0
    for index, speech in enumerate(marks):
        if speech:
            if cur_start is None:
                cur_start = index
            silence_run = 0
        elif cur_start is not None:
            silence_run += 1
            if silence_run >= join_frames:
                spans.append((cur_start, index - silence_run + 1))
                cur_start = None
                silence_run = 0
    if cur_start is not None:
        spans.append((cur_start, len(marks)))

    final: list[tuple[int, int]] = []
    for span_start, span_end in spans:
        seg_start = span_start
        while span_end - seg_start > max_frames:
            cut = seg_start + max_frames
            final.append((seg_start, cut))
            seg_start = cut
        final.append((seg_start, span_end))

    merged: list[tuple[int, int]] = []
    for start, end in final:
        if merged and end - merged[-1][1] < min_frames and end - merged[-1][0] <= max_frames:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return [(start * VAD_FRAME_MS, end * VAD_FRAME_MS) for start, end in merged]


def _segments_for(path: Path) -> list[tuple[int, int]] | None:
    duration_ms = _parse_wav_duration(path)
    if duration_ms <= MAX_SEG_S * 1000:
        return None
    try:
        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            if (
                rate not in (8000, 16000, 32000, 48000)
                or wf.getsampwidth() != 2
                or wf.getnchannels() != 1
            ):
                raise AsrError(
                    "LONG_WAV_UNSUPPORTED",
                    "长 WAV 分段仅支持 8k/16k/32k/48k、16bit、单声道",
                )
            pcm = wf.readframes(wf.getnframes())
    except AsrError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise AsrError("INVALID_AUDIO", "长 WAV 无法读取或文件已损坏") from exc
    return _split_marks_to_segments(_vad_frame_marks(pcm, rate))


def _extract_segment_pcm(path: Path, start_ms: int, end_ms: int) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        wf.setpos(int(start_ms * rate / 1000))
        frame_count = int((end_ms - start_ms) * rate / 1000)
        return wf.readframes(frame_count), rate


def transcribe(
    audio_path: str | Path,
    preferred: str = "auto",
    *,
    enhance_emotion: bool | None = None,
) -> AsrResult:
    """统一入口：多格式短音频直传；长 WAV 经 VAD 分段后合并。"""
    path = Path(audio_path)
    audio = inspect_audio(path)
    if audio.audio_format != "wav" and audio.size_bytes > MAX_AUDIO_BYTES:
        raise AsrError(
            "AUDIO_TOO_LARGE",
            "超过 8MB 的压缩音频暂不支持本地分段，请先切分或转为 WAV",
        )
    if audio.audio_format == "wav" and _wav_is_digital_silence(path):
        return _no_speech_result(audio)

    # J-2 噪音降权：WAV 才能算 SNR（压缩格式不参与降权，维持默认 high）
    snr_db = estimate_snr(path) if audio.audio_format == "wav" else None
    noise_weight = _noise_weight(snr_db)

    segments = _segments_for(path) if audio.audio_format == "wav" else None
    if segments == []:
        return _no_speech_result(audio)
    if segments is None:
        result = _transcribe_one(
            path,
            preferred,
            [],
            enhance_emotion=enhance_emotion,
        )
        result.snr_db = snr_db
        result.noise_weight = noise_weight
        result.emotion_merge = single_segment_emotion_merge(
            result, noise_weight=noise_weight
        )
        return result

    import tempfile

    texts: list[str] = []
    results: list[AsrResult] = []
    failures: list[AsrError] = []
    for index, (start_ms, end_ms) in enumerate(segments, 1):
        seg_path: Path | None = None
        try:
            pcm, rate = _extract_segment_pcm(path, start_ms, end_ms)
            if not pcm:
                continue
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                with wave.open(tmp, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(rate)
                    wf.writeframes(pcm)
                seg_path = Path(tmp.name)
            result = _transcribe_one(
                seg_path,
                preferred,
                [],
                enhance_emotion=enhance_emotion,
            )
            results.append(result)
            if result.text:
                texts.append(result.text)
        except AsrError as exc:
            failures.append(exc)
            logger.warning("ASR 分段 %d 失败: %s", index, exc.code)
        finally:
            if seg_path is not None:
                seg_path.unlink(missing_ok=True)

    if failures:
        raise AsrError(
            "ASR_PARTIAL_FAILURE",
            "长录音存在未成功转写的分段",
            retryable=any(item.retryable for item in failures),
            errors=[item.code for item in failures],
        )
    if not texts:
        return _no_speech_result(audio)

    first = results[0]
    mock_used = any(item.mock for item in results)

    # J-3 段级情绪合并（对齐 B5a §3）：主导 = 时长最长段；峰值保留为标记
    dominant = max(results, key=lambda item: item.duration_ms)
    merge = merge_segment_emotion(results, noise_weight=noise_weight)
    return AsrResult(
        text="".join(texts),
        channel="mock" if mock_used else first.channel,
        outcome="mock" if mock_used else "succeeded",
        emotion=dominant.emotion,
        emotion_confidence=dominant.emotion_confidence,
        emotion_source=dominant.emotion_source,
        emotion_model=dominant.emotion_model,
        confidence=sum(item.confidence for item in results) / len(results),
        duration_ms=audio.duration_ms,
        mock=mock_used,
        model=first.model,
        provider=first.provider,
        provider_request_id=first.provider_request_id,
        audio_format=audio.audio_format,
        source_audio_sha256=audio.sha256,
        # J-1 音频事件跨段合并（去重并集）＋ J-2 SNR/权重 + J-3 合并结构
        audio_events=list(
            dict.fromkeys(
                event for item in results for event in item.audio_events
            )
        ),
        emotion_bonus=any(item.emotion_bonus for item in results),
        silence_hint=any(item.silence_hint for item in results),
        not_oral=any(item.not_oral for item in results),
        snr_db=snr_db,
        noise_weight=noise_weight,
        emotion_merge=merge,
        segments=[segment for item in results for segment in item.segments],
        usage={"segments": [item.usage for item in results if item.usage]},
        errors=[error for item in results for error in item.errors],
    )
