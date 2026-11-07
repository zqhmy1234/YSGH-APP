"""ASR 音频处理/VAD（F7 拆包）：上传校验、文件检查、SNR 估计与长录音分段。

手机常见 M4A/MP3/AAC 等格式由 Fun-ASR Flash 通道直接处理；WAV 额外保留
数字静音识别、SNR 估计与长录音 VAD 分段能力。
"""
from __future__ import annotations

import hashlib
import math
import wave
from array import array
from pathlib import Path

from .models import AsrError, AudioInfo

MAX_AUDIO_BYTES = 8 * 1024 * 1024

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

# 噪音降权（J-2）：轻量 SNR 检测输入参数（阈值 NOISE_SNR_THRESHOLD_DB 在 emotion.py）
NOISE_FLOOR_PERCENTILE = 10.0
SIGNAL_PERCENTILE = 90.0
SNR_FRAME_MS = 20

# 长录音 VAD 分段（仅 WAV 16bit 单声道）
VAD_FRAME_MS = 10
MIN_SEG_S = 120
MAX_SEG_S = 240
SILENCE_JOIN_S = 1.5


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
