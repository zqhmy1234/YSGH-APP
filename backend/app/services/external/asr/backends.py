"""ASR 后端适配（F7 拆包）：Fun-ASR Flash（百炼）、SenseVoice 本地降级、mock。

跨子模块的可替换点（_http_post_json / _call_with_retry / _infer_sensevoice）经
包命名空间 `_asr.<name>` 调用——测试 monkeypatch 打在包（app.services.external.asr）
上即可生效，拆包前后行为等价。
"""
from __future__ import annotations

import base64
import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib import error, request

from app.core.config import settings

from .. import asr as _asr
from .audio import inspect_audio
from .emotion import (
    SENSEVOICE_EMOTION_TAGS,
    SENSEVOICE_UNKNOWN_EMOTION_TAG,
    _parse_audio_events,
    apply_audio_event_effects,
)
from .models import (
    DEFAULT_DASHSCOPE_BASE_URL,
    MODEL_FUNASR,
    MODEL_SENSEVOICE,
    MODEL_SENSEVOICE_TOKENIZER,
    AsrError,
    AsrResult,
    SenseVoiceResult,
)

logger = logging.getLogger("yishu.asr")

ASR_CHANNELS = ("funasr", "sensevoice")

_sensevoice_state: dict[str, Any | None] = {"model": None}
_sensevoice_model_lock = threading.Lock()


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


def _build_flash_payload(audio) -> dict[str, Any]:
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
    _, response = _asr._call_with_retry(
        lambda: _asr._http_post_json(
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


# ---- B5a · Wave4 AgentJ：SenseVoice 本地推理 + 音频事件/噪音/段级情绪合并 ----

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
    local = _asr._infer_sensevoice(path)
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


_CHANNELS = {
    "funasr": _transcribe_funasr,
    "sensevoice": _transcribe_sensevoice,
}
