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

# 百炼 ASR 模型
MODEL_FUNASR = "paraformer-v2"       # FunASR 家族：语义内容
MODEL_SENSEVOICE = "sensevoice-v1"   # 声学情绪 + 内容

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


def _emotion_label(raw: str | None) -> str:
    """SenseVoice emotion 字段 → 中文标签（未识别/缺失 → 平静）"""
    if not raw:
        return "平静"
    return EMOTION_MAP.get(raw.strip().lower(), "平静")


def _transcribe_funasr(path: Path) -> AsrResult:
    """通道 A：FunASR（paraformer-v2）语义转写"""
    from dashscope.audio.asr import Recognition

    resp = Recognition.call(
        model=MODEL_FUNASR,
        file=str(path),
        format="wav",
        sample_rate=16000,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"funasr 调用失败: {resp.status_code} {resp.message}")
    sentences = getattr(resp.get_sentence(), "sentence", None) or []
    text = "".join(s.get("text", "") for s in sentences).strip()
    if not text:
        raise RuntimeError("funasr 返回空转写")
    return AsrResult(
        text=text,
        channel="funasr",
        emotion="平静",
        confidence=float(resp.get_sentence().sentence[0].get("confidence", 0.0)) if sentences else 0.0,
        duration_ms=_parse_wav_duration(path),
    )


def _transcribe_sensevoice(path: Path) -> AsrResult:
    """通道 B：SenseVoice（sensevoice-v1）声学情绪 + 内容"""
    from dashscope.audio.asr import Recognition

    resp = Recognition.call(
        model=MODEL_SENSEVOICE,
        file=str(path),
        format="wav",
        sample_rate=16000,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"sensevoice 调用失败: {resp.status_code} {resp.message}")
    sentences = getattr(resp.get_sentence(), "sentence", None) or []
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


def transcribe(audio_path: str | Path, preferred: str = "auto") -> AsrResult:
    """转写主入口：preferred 通道优先，失败自动降级，最终 mock 兜底。

    preferred: auto（funasr→sensevoice）/ funasr / sensevoice / mock
    """
    path = Path(audio_path)
    if not path.is_file():
        raise FileNotFoundError(f"音频文件不存在: {path}")

    order = list(ASR_CHANNELS) if preferred == "auto" else [preferred] + [
        c for c in ASR_CHANNELS if c != preferred
    ]
    errors: list[str] = []
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
