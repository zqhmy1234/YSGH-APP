"""ASR 情绪标签（F7 拆包）：SenseVoice 情绪映射、音频事件消费、噪音降权与段级合并。"""
from __future__ import annotations

import re
from typing import Any

from .models import EMOTION_ACTION_THRESHOLD, AsrResult

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

# 噪音降权（J-2）：低于阈值 → 声学情绪权重降为与语义持平
NOISE_SNR_THRESHOLD_DB = 15.0


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
        if result.emotion in (None, "平静") or result.emotion_confidence < EMOTION_ACTION_THRESHOLD:
            if result.emotion in (None, "平静"):  # D-16: 未测得(None)与平静同样被笑声提升为开心
                result.emotion = "开心"
                result.emotion_confidence = max(result.emotion_confidence, 0.6)
                if result.emotion_source in {"", "none"}:
                    result.emotion_source = "audio_event_laughter"
    if AUDIO_EVENT_SILENCE in events:
        result.silence_hint = True
    if AUDIO_EVENT_ENVIRONMENT in events:
        result.not_oral = True


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
