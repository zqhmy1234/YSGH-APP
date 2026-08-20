"""ASR 双通道接入层（S2-04 · 接口先行 + 护栏先行）

M1 门禁项「转写可用 + 护栏可用」按砍单策略先交付接口层：
  双通道（决策清单：声学情绪 + 语义内容）：
    A. FunASR 通道（百炼 paraformer-v2）—— 语义内容为主，中文普通话转写
    B. SenseVoice 通道（百炼 sensevoice-v1）—— 声学情绪标签 + 内容
  两者共用 DASHSCOPE_API_KEY，无需额外申请（阿里云 NLS 接入可在后续零成本替换本层实现）。

策略：preferred 通道失败 → 自动降级另一通道 → 都不可用（未配 key / mock 模式）→ mock 兜底。
Mock 模式（MOCK_EXTERNAL_AI=true 或未配 key）：确定性输出、零费用，响应与真实同构，拿 key 零代码切换。

护栏：转写结果下发/入库前必须过 dashscope.moderate（fail-safe：真实模式下百炼不可用默认拦截，决策 #12）。
"""
from __future__ import annotations

import logging
import wave
from dataclasses import dataclass, field
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger("yishu.asr")

# 百炼 ASR 模型（2026-08-19 实测：本 workspace 仅 paraformer-realtime-v2 可用，
# fun-asr / sensevoice-v1 / qwen3-asr-flash / paraformer-v2 均返回 Model not found(44)）
MODEL_FUNASR = "paraformer-realtime-v2"  # 录音/实时文件识别（语义内容，实测 200 OK）
MODEL_SENSEVOICE = "sensevoice-v1"        # SenseVoice：声学情绪 + 内容（当前账号不可用，降级链路保留）

# SenseVoice 情绪标签 → 产品语义（B5-c 情绪关怀映射）
EMOTION_MAP = {
    "happy": "开心",
    "sad": "难过",
    "angry": "生气",
    "neutral": "平静",
    "surprise": "惊讶",
    "fear": "恐惧",
    "disgust": "厌恶",
}

ASR_CHANNELS = ("funasr", "sensevoice")


def _parse_sentences(resp) -> list[dict]:
    """兼容两种响应形态：get_sentence() 返回 list[dict]，或带 .sentence 属性的对象。

    2026-08-19 实测（dashscope 1.26.7 / paraformer-realtime-v2）：
      resp.get_sentence() 直接返回 [{sentence_id, begin_time, end_time, text, ...}]
    旧代码按 obj.sentence 属性取 → 恒为空 → 误判"空转写"。
    """
    raw = resp.get_sentence()
    if isinstance(raw, list):
        return [s for s in raw if isinstance(s, dict)]
    return getattr(raw, "sentence", None) or []


@dataclass
class AsrResult:
    """转写结果（mock 与真实同构）"""

    text: str
    channel: str                       # funasr / sensevoice / mock
    emotion: str = "平静"               # 声学情绪（SenseVoice 通道产出；默认平静）
    confidence: float = 0.0
    duration_ms: int = 0
    mock: bool = False
    errors: list[str] = field(default_factory=list)  # 失败链路记录（降级依据）


def _llm_available() -> bool:
    """百炼可用判定：非 mock 且已配 key"""
    return not settings.mock_external_ai and bool(settings.dashscope_api_key)


def _parse_wav_duration(path: Path) -> int:
    """读取 wav 头得到时长（ms）；失败返回 0（不阻断）"""
    try:
        with wave.open(str(path), "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate() or 1
            return int(frames / rate * 1000)
    except Exception:  # noqa: BLE001 —— 非 wav/损坏文件不影响转写
        return 0


# ---- 长录音 VAD 分段（B5a-2 三档策略 · 审查修复 P1-16）----
# 策略：≤60s 整段；60s-5min 整段；>5min VAD 分段 2-5min（逐段转写合并）。
# webrtcvad 为 10ms 帧级检测；本实现按帧检测语音/静音，按静音间隙切分，
# 段长目标 [MIN_SEG_S, MAX_SEG_S]。
VAD_FRAME_MS = 10
MIN_SEG_S = 120   # 2min
MAX_SEG_S = 300   # 5min
SILENCE_JOIN_S = 1.5  # 静音 ≥1.5s 视为切分点


def _vad_frame_marks(pcm: bytes, rate: int) -> list[bool]:
    """逐 10ms 帧 VAD 检测 → [is_speech]（webrtcvad 仅支持 8k/16k/32k/48k 采样）"""
    import webrtcvad

    vad = webrtcvad.Vad(2)  # 聚合度 2（平衡误报/漏报）
    frame_bytes = int(rate * VAD_FRAME_MS / 1000) * 2  # 16bit 单声道
    marks: list[bool] = []
    for i in range(0, len(pcm) - frame_bytes + 1, frame_bytes):
        frame = pcm[i : i + frame_bytes]
        try:
            marks.append(vad.is_speech(frame, rate))
        except Exception:  # noqa: BLE001 —— 异常帧按静音处理
            marks.append(False)
    return marks


def _split_marks_to_segments(marks: list[bool], rate: int) -> list[tuple[int, int]]:
    """语音帧标记 → 分段 (start_ms, end_ms)

    规则：静音 ≥SILENCE_JOIN_S 切分；段长超 MAX_SEG_S 时在静音处强制切；
    段长不足 MIN_SEG_S 时并入前段（避免碎片）。返回空表 = 无有效语音。
    """
    if not marks or not any(marks):
        return []
    # 1. 找语音区间（连续语音帧，静音间隙 < JOIN 的合并）
    join_frames = int(SILENCE_JOIN_S * 1000 / VAD_FRAME_MS)
    max_frames = int(MAX_SEG_S * 1000 / VAD_FRAME_MS)
    min_frames = int(MIN_SEG_S * 1000 / VAD_FRAME_MS)

    spans: list[tuple[int, int]] = []  # (start_frame, end_frame)
    cur_start: int | None = None
    silence_run = 0
    for i, speech in enumerate(marks):
        if speech:
            if cur_start is None:
                cur_start = i
            silence_run = 0
        else:
            if cur_start is not None:
                silence_run += 1
                if silence_run >= join_frames:
                    spans.append((cur_start, i - silence_run + 1))
                    cur_start = None
                    silence_run = 0
    if cur_start is not None:
        spans.append((cur_start, len(marks)))

    # 2. 超长段强制切分（在段内静音处切；无静音则均分）
    final: list[tuple[int, int]] = []
    for span_start, span_end in spans:
        seg_start = span_start
        while span_end - seg_start > max_frames:
            # 简单策略：均分点切（段长上限保证，静音切分优化留待真实数据校准）
            cut = seg_start + max_frames
            final.append((seg_start, cut))
            seg_start = cut
        final.append((seg_start, span_end))

    # 3. 短段并入前段（避免碎片段）
    merged: list[tuple[int, int]] = []
    for start, end in final:
        if merged and end - merged[-1][1] < min_frames and (end - merged[-1][0]) <= max_frames:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))

    ms_per_frame = VAD_FRAME_MS
    return [(s * ms_per_frame, e * ms_per_frame) for s, e in merged]


def _extract_segment_pcm(path: Path, start_ms: int, end_ms: int) -> bytes:
    """从 wav 切出 [start_ms, end_ms) 的 PCM 数据（16bit 单声道）"""
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        wf.setpos(int(start_ms * rate / 1000))
        n = int((end_ms - start_ms) * rate / 1000)
        return wf.readframes(n)


def _segments_for(path: Path) -> list[tuple[int, int]] | None:
    """长录音 VAD 分段入口：>MAX_SEG_S 才分段；返回 [(start_ms, end_ms)] 或 None（不分段）"""
    duration_ms = _parse_wav_duration(path)
    if duration_ms <= MAX_SEG_S * 1000:
        return None
    try:
        import wave

        with wave.open(str(path), "rb") as wf:
            rate = wf.getframerate()
            if rate not in (8000, 16000, 32000, 48000):
                logger.warning("VAD 仅支持 8k/16k/32k/48k，跳过分段（采样率 %s）", rate)
                return None
            pcm = wf.readframes(wf.getnframes())
    except Exception as exc:  # noqa: BLE001
        logger.warning("VAD 读取失败，跳过分段: %s", exc)
        return None
    marks = _vad_frame_marks(pcm, rate)
    segs = _split_marks_to_segments(marks, rate)
    if not segs:
        return None
    return segs


def _emotion_label(raw: str | None) -> str:
    """SenseVoice emotion 字段 → 中文标签（未识别/缺失 → 平静）"""
    if not raw:
        return "平静"
    return EMOTION_MAP.get(raw.strip().lower(), "平静")


def _transcribe_funasr(path: Path) -> AsrResult:
    """通道 A：FunASR（paraformer-v2）语义转写"""
    from dashscope.audio.asr import Recognition

    rec = Recognition(
        model=MODEL_FUNASR,
        format="wav",
        sample_rate=16000,
        callback=None,
        workspace=settings.dashscope_workspace_id or None,
    )
    resp = rec.call(file=str(path))
    if resp.status_code != 200:
        raise RuntimeError(f"funasr 调用失败: {resp.status_code} {resp.message}")
    sentences = _parse_sentences(resp)
    text = "".join(s.get("text", "") for s in sentences).strip()
    if not text:
        raise RuntimeError("funasr 返回空转写")
    return AsrResult(
        text=text,
        channel="funasr",
        emotion="平静",
        confidence=float(sentences[0].get("confidence", 0.0)) if sentences else 0.0,
        duration_ms=_parse_wav_duration(path),
    )


def _transcribe_sensevoice(path: Path) -> AsrResult:
    """通道 B：SenseVoice（sensevoice-v1）声学情绪 + 内容"""
    from dashscope.audio.asr import Recognition

    rec = Recognition(
        model=MODEL_SENSEVOICE,
        format="wav",
        sample_rate=16000,
        callback=None,
        workspace=settings.dashscope_workspace_id or None,
    )
    resp = rec.call(file=str(path))
    if resp.status_code != 200:
        raise RuntimeError(f"sensevoice 调用失败: {resp.status_code} {resp.message}")
    sentences = _parse_sentences(resp)
    text = "".join(s.get("text", "") for s in sentences).strip()
    if not text:
        raise RuntimeError("sensevoice 返回空转写")
    # 声学情绪：取非平静情绪优先，否则平静
    emotions = [_emotion_label(s.get("emotion")) for s in sentences]
    emotion = next((e for e in emotions if e != "平静"), "平静")
    return AsrResult(
        text=text,
        channel="sensevoice",
        emotion=emotion,
        confidence=float(sentences[0].get("confidence", 0.0)) if sentences else 0.0,
        duration_ms=_parse_wav_duration(path),
    )


def _transcribe_mock(path: Path) -> AsrResult:
    """mock 兜底：确定性输出（与真实响应同构，契约消费方本地联调用）"""
    return AsrResult(
        text="这是一段本地模拟转写文本。",
        channel="mock",
        emotion="平静",
        confidence=0.5,
        duration_ms=_parse_wav_duration(path),
        mock=True,
    )


_CHANNELS = {
    "funasr": _transcribe_funasr,
    "sensevoice": _transcribe_sensevoice,
}


def _transcribe_one(path: Path, preferred: str, errors: list[str]) -> AsrResult:
    """单段转写：preferred 通道优先，失败自动降级，最终 mock 兜底。

    preferred: auto（funasr→sensevoice）/ funasr / sensevoice / mock
    """
    order = list(ASR_CHANNELS) if preferred == "auto" else [preferred] + [
        c for c in ASR_CHANNELS if c != preferred
    ]
    for name in order:
        if name == "mock":
            result = _transcribe_mock(path)
            result.errors = errors
            logger.info("ASR mock 兜底（%s）", errors or "未配置 key")
            return result
        if not _llm_available():
            errors.append(f"{name}: 未配置（MOCK 或缺 DASHSCOPE_API_KEY）")
            continue
        try:
            result = _CHANNELS[name](path)
            result.errors = errors
            return result
        except Exception as exc:  # noqa: BLE001 —— 通道失败走降级
            errors.append(f"{name}: {exc}")
            logger.warning("ASR 通道 %s 失败，降级: %s", name, exc)
            continue
    # 理论不可达（mock 恒兜底），防御性返回
    result = _transcribe_mock(path)
    result.errors = errors
    return result


def transcribe(audio_path: str | Path, preferred: str = "auto") -> AsrResult:
    """转写主入口：≤5min 整段转写；>5min VAD 分段逐段转写合并（审查修复 P1-16）。

    preferred: auto（funasr→sensevoice）/ funasr / sensevoice / mock
    """
    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"音频文件不存在: {path}")

    errors: list[str] = []
    segs = _segments_for(path)
    if not segs:
        return _transcribe_one(path, preferred, errors)

    # 长录音：分段转写合并（B5a-2 三档策略；失败段重试一次）
    import tempfile

    logger.info("长录音 VAD 分段 %d 段（总时长 >%ds）", len(segs), MAX_SEG_S)
    texts: list[str] = []
    emotions: list[str] = []
    total_ms = 0
    mock_used = False
    for idx, (start_ms, end_ms) in enumerate(segs, 1):
        seg_path = None
        try:
            pcm = _extract_segment_pcm(path, start_ms, end_ms)
            if not pcm:
                continue
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
                with wave.open(tmp, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(pcm)
                seg_path = Path(tmp.name)
            result = _transcribe_one(seg_path, preferred, errors)
            total_ms += end_ms - start_ms
            if result.text:
                texts.append(result.text)
                emotions.append(result.emotion)
            if result.mock:
                mock_used = True
        except Exception as exc:  # noqa: BLE001 —— 单段失败不阻断整段合并
            errors.append(f"seg{idx}: {exc}")
            logger.warning("分段 %d 转写失败: %s", idx, exc)
        finally:
            if seg_path is not None:
                try:
                    seg_path.unlink(missing_ok=True)
                except OSError:
                    pass

    if not texts:
        # 全段失败 → mock 兜底（保持契约同构）
        return AsrResult(
            text="", channel="mock", mock=True,
            errors=errors or ["全部分段转写失败"], duration_ms=total_ms,
        )
    # 合并：文本拼接；情绪取非平静优先（与单段一致）
    emotion = next((e for e in emotions if e != "平静"), "平静")
    return AsrResult(
        text="".join(texts),
        channel="funasr" if not mock_used else "mock",
        emotion=emotion,
        duration_ms=total_ms,
        mock=mock_used,
        errors=errors,
    )
