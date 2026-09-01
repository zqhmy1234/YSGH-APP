"""ASR 转写编排（F7 拆包）：统一入口、通道选择/降级、VAD 分段合并与情绪增强。"""
from __future__ import annotations

import logging
import wave
from pathlib import Path

from app.core.config import settings

from .. import asr as _asr
from .audio import (
    MAX_AUDIO_BYTES,
    _extract_segment_pcm,
    _segments_for,
    _wav_is_digital_silence,
    estimate_snr,
    inspect_audio,
)
from .backends import _CHANNELS, ASR_CHANNELS, _transcribe_mock
from .emotion import (
    _noise_weight,
    apply_audio_event_effects,
    merge_segment_emotion,
    single_segment_emotion_merge,
)
from .models import (
    MODEL_SENSEVOICE,
    AsrError,
    AsrResult,
    AudioInfo,
)

logger = logging.getLogger("yishu.asr")


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
        local = _asr._infer_sensevoice(path)
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
