"""百度 OCR 服务（PHOTO-1 · 2026-09-26 接入照片管线）

迁自 codex/photo-merge @7b192c3 的 `external/ocr.py`（2026-08 实测 205 张链路），
按台账 PHOTO-1 要求落位 `baidu_ocr.py`：

- accurate_basic 高精度识别 + CHN_ENG
- 大图前置压缩（最长边 2000px）规避百度 216205 input oversize
- access_token 进程内缓存（过期前 10 分钟刷新）
- 统一走 settings（.env BAIDU_API_KEY / BAIDU_SECRET_KEY），不硬编码密钥
- 网络抖动/5xx 走统一 `with_retry`（3 次指数退避）；鉴权缺失/业务错误显式抛错不静默
"""
from __future__ import annotations

import base64
import io
import logging
import time

import httpx
from PIL import Image

from app.core.config import settings
from app.services.external.retry import with_retry

logger = logging.getLogger("yishu.external.ocr")

TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"  # noqa: S105 —— 接口地址，非口令（名字含 TOKEN 触发误报）
OCR_URL = "https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic"
OCR_LANGUAGE = "CHN_ENG"
# 最长边上限（百度 OCR 图片大小限制；超限返回 216205）
MAX_OCR_SIDE = 2000

_token_cache: dict = {"token": None, "expire_at": 0.0}


class OcrNotConfiguredError(RuntimeError):
    """OCR 凭据缺失（与 image_caption 的「配置缺失」显式路径同语义，调用方可按此降级）"""


def get_access_token() -> str:
    """百度 access_token（进程内缓存，过期前 600s 自动刷新）"""
    api_key = (settings.baidu_api_key or "").strip()
    secret_key = (settings.baidu_secret_key or "").strip()
    if not api_key:
        raise OcrNotConfiguredError("未配置 BAIDU_API_KEY：请在 backend/.env 填写")
    if not secret_key:
        raise OcrNotConfiguredError("未配置 BAIDU_SECRET_KEY：请在 backend/.env 填写")

    now = time.time()
    if _token_cache["token"] and now < _token_cache["expire_at"] - 600:
        return _token_cache["token"]

    resp = httpx.get(
        TOKEN_URL,
        params={
            "grant_type": "client_credentials",
            "client_id": api_key,
            "client_secret": secret_key,
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expire_at"] = now + float(data.get("expires_in", 2592000))
    return _token_cache["token"]


@with_retry(retries=3, backoff=(1, 2, 4), timeout=30)
def ocr_image(image_bytes: bytes) -> str:
    """传入图片字节，返回识别文字（按行拼接，行间 \\n）；失败显式抛错不静默

    调用方（pipeline_photo）负责捕获降级：OCR 是增强项，失败不影响照片主流程。
    """
    prepared = _prepare_for_ocr(image_bytes)
    params = {"access_token": get_access_token()}
    payload = {
        "image": base64.b64encode(prepared).decode("ascii"),
        "language_type": OCR_LANGUAGE,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    resp = httpx.post(OCR_URL, params=params, data=payload, headers=headers, timeout=45)
    resp.raise_for_status()
    data = resp.json()
    if "error_code" in data:
        raise RuntimeError(f"百度OCR错误 {data.get('error_code')}: {data.get('error_msg')}")
    return "\n".join(item["words"] for item in data.get("words_result", []))


def _prepare_for_ocr(data: bytes) -> bytes:
    """转 RGB + 最长边压缩到 2000px + JPEG 重编码（防 216205 oversize）

    图片字节无法解析（损坏/非图片）属**输入错误**，改抛普通 RuntimeError——
    PIL 的 UnidentifiedImageError 继承自 OSError，会被 with_retry 误判为网络抖动
    而重试 3 次（白白等待且污染日志语义）；输入错误不应重试。
    """
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"图片字节无法解析（非图片或已损坏）: {exc}") from exc
    if max(img.size) > MAX_OCR_SIDE:
        img.thumbnail((MAX_OCR_SIDE, MAX_OCR_SIDE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()
