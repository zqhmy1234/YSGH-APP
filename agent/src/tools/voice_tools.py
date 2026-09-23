"""
语音转文字工具模块
处理微信语音消息的下载、格式转换和ASR识别
"""
import base64
import logging
import os
import subprocess
import tempfile

from coze_coding_dev_sdk import ASRClient
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context

logger = logging.getLogger(__name__)

# AMR 文件头标识
AMR_HEADER = b"#!AMR"

# ASR 支持的格式: WAV/MP3/OGG OPUS/M4A (不支持 AMR)


def _convert_amr_to_mp3(audio_bytes: bytes) -> bytes:
    """
    使用 ffmpeg 将 AMR 格式转换为 MP3 格式
    ASR 不支持 AMR，需要先转为 MP3
    """
    with tempfile.NamedTemporaryFile(suffix=".amr", delete=False) as amr_file:
        amr_file.write(audio_bytes)
        amr_path = amr_file.name

    mp3_path = amr_path.replace(".amr", ".mp3")
    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", amr_path,
                "-codec:a", "libmp3lame",
                "-b:a", "128k",
                "-ar", "16000",
                mp3_path,
            ],
            capture_output=True,
            timeout=30,
        )
        if not os.path.exists(mp3_path) or os.path.getsize(mp3_path) == 0:
            raise RuntimeError("ffmpeg 转换失败，输出文件为空")

        with open(mp3_path, "rb") as f:
            return f.read()
    finally:
        for path in (amr_path, mp3_path):
            if os.path.exists(path):
                os.unlink(path)


def _ensure_asr_format(audio_bytes: bytes, filename: str | None = None) -> bytes:
    """
    确保音频格式是 ASR 支持的格式 (MP3/WAV/OGG/M4A)
    如果是 AMR 则转换为 MP3
    """
    # 检查 AMR 头
    if audio_bytes[:5] == AMR_HEADER:
        logger.info("检测到 AMR 格式，正在转换为 MP3")
        return _convert_amr_to_mp3(audio_bytes)

    # 通过文件扩展名判断
    if filename:
        ext = os.path.splitext(filename)[1].lower()
        if ext == ".amr":
            logger.info("根据文件名检测到 AMR 格式，正在转换为 MP3")
            return _convert_amr_to_mp3(audio_bytes)

    # 其他格式 (MP3/WAV/OGG/M4A) 直接使用
    return audio_bytes


def transcribe_voice(
    audio_bytes: bytes,
    user_id: str,
    filename: str | None = None,
) -> str:
    """
    语音转文字

    :param audio_bytes: 音频原始字节
    :param user_id: 用户ID
    :param filename: 原始文件名（可选，用于判断格式）
    :return: 识别出的文字
    """
    ctx = request_context.get() or new_context(method="asr.recognize")

    try:
        asr_client = ASRClient(ctx=ctx)

        # 确保格式兼容
        audio_bytes = _ensure_asr_format(audio_bytes, filename)

        # Base64 编码
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")

        # ASR 识别
        text, _ = asr_client.recognize(
            uid=user_id,
            base64_data=audio_b64,
        )

        logger.info(f"语音识别成功: {text[:50] if text else '(空)'}")
        return text or ""

    except Exception as e:
        logger.error(f"语音识别失败: {e}")
        raise
