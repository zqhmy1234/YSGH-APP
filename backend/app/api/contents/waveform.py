"""BB3：真实音频波形（2026-09-08）

只读端点：GET /api/v1/contents/{id}/waveform?buckets=N（挂在共享 serializers.router）
客户端 VoiceWave 组件真实波形数据源（替代 genWaveHeights 确定性伪波形的升级通路）。
缓存取舍：语音条 ≤60s（16kHz/16bit/单声道 ≈1.9MB），解析为纯内存分桶峰值采样，
现算成本毫秒级，低于缓存失效/存储复杂度——故每次现算不缓存（量大后再议 LRU）。

本模块由原单文件 app/api/contents.py 拆出（重构波 B2），端点逻辑未变。
"""
import array
import logging
import struct
import sys

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.api.contents.serializers import router
from app.api.deps import get_current_user, load_alive_content, uuid4_str
from app.core.errors import ERR_MEDIA_003, ApiError
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.services.external.storage import StorageError, get_storage_backend

logger = logging.getLogger("yishu.contents")

_WAVEFORM_MAX_BYTES = 16 * 1024 * 1024  # 防御上限：60s 语音 ≈1.9MB，远超真实值才触发


def _wav_bucket_peaks(data: bytes, buckets: int) -> list[float]:
    """解析 WAV（RIFF/PCM16）→ buckets 个 0..1 峰值。

    容错：chunk 遍历找 data chunk（不认死 44 字节标准偏移，兼容扩展头/前缀附注块）。
    非 RIFF/WAVE、缺 fmt/data、非 PCM16、样本为空 → ValueError（调用方映射 404 显式报错）。
    """
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not RIFF/WAVE")
    audio_format: int | None = None
    bits: int | None = None
    pcm: bytes | None = None
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        (chunk_size,) = struct.unpack_from("<I", data, pos + 4)
        body = data[pos + 8 : pos + 8 + chunk_size]
        if chunk_id == b"fmt " and len(body) >= 16:
            audio_format, _ch, _rate, _br, _align, bits = struct.unpack_from("<HHIIHH", body)
        elif chunk_id == b"data":
            pcm = body
            break  # data 一般是最后的有效块（fmt 在前），提前止省一轮遍历
        pos += 8 + chunk_size + (chunk_size & 1)  # chunk 按 2 字节对齐
    if audio_format != 1 or bits != 16:
        raise ValueError("not PCM16")
    if not pcm:
        raise ValueError("empty data chunk")
    samples = array.array("h")
    usable = len(pcm) - len(pcm) % 2
    samples.frombytes(pcm[:usable])
    if sys.byteorder == "big":
        samples.byteswap()
    total = len(samples)
    step = total / buckets
    peaks: list[float] = []
    for b in range(buckets):
        start = int(b * step)
        end = min(max(start + 1, int((b + 1) * step)), total)
        if start >= total:
            peaks.append(0.0)  # 样本数 < 桶数：尾部桶补 0（不静默截断桶数）
            continue
        peak = max(abs(v) for v in samples[start:end])
        peaks.append(peak / 32768.0)
    return peaks


@router.get("/{content_id}/waveform", response_model=ApiResponse[dict])
def get_content_waveform(
    content_id: str,
    buckets: int = Query(default=28, ge=1, le=128),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """内容音频真实波形（BB3 · 2026-09-08 新增）

    客户端 VoiceWave 组件数据源：读语音原件 → 解析 WAV data chunk →
    buckets 桶峰值（0..1 浮点，客户端映射到条高渲染）。

    - 归属校验：不存在/已删除/非本人 → 404 CONTENT_010（load_alive_content 同款 IDOR 防护）
    - 非语音内容 / cos_key 缺失 / 对象读取失败 / 非 PCM16 WAV → 404 MEDIA_003
      「音频不可用」（与 media.get_content_audio 同口径：对外不区分原因防探测，
      服务端日志留真因；显式错误码非静默）
    - buckets 参数由 FastAPI Query 校验（1..128，越界自动 422 信封）
    - 不缓存：语音 ≤60s 现算毫秒级，见上方 BB3 注释
    """
    cid = uuid4_str(content_id)
    row = load_alive_content(db, user.id, cid)
    if row.content_type != "voice" or not row.cos_key:
        raise ApiError(ERR_MEDIA_003, "音频不可用", http=404)
    try:
        data = get_storage_backend().get_object(row.cos_key)
    except (KeyError, ValueError) as exc:
        logger.info("波形音频对象不可用 key=%s err=%s", row.cos_key, exc)
        raise ApiError(ERR_MEDIA_003, "音频不可用", http=404) from exc
    except StorageError as exc:
        logger.warning("波形音频读取失败（存储故障）key=%s code=%s", row.cos_key, exc.code)
        raise ApiError(ERR_MEDIA_003, "音频不可用", http=404) from exc
    if len(data) > _WAVEFORM_MAX_BYTES:
        logger.warning("波形音频超防御上限 key=%s size=%d", row.cos_key, len(data))
        raise ApiError(ERR_MEDIA_003, "音频不可用", http=404)
    try:
        peaks = _wav_bucket_peaks(data, buckets)
    except ValueError as exc:
        logger.info("波形解析失败 content_id=%s reason=%s", cid, exc)
        raise ApiError(ERR_MEDIA_003, "音频不可用", http=404) from exc
    return ApiResponse(data={"buckets": [round(p, 4) for p in peaks]})
