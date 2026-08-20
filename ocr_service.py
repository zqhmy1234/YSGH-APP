import base64
import io
import os
import time

import requests
from dotenv import load_dotenv
from PIL import Image

load_dotenv()

TOKEN_URL = "https://aip.baidubce.com/oauth/2.0/token"
OCR_URL = os.getenv("BAIDU_OCR_URL", "https://aip.baidubce.com/rest/2.0/ocr/v1/general_basic")
OCR_LANGUAGE = os.getenv("BAIDU_OCR_LANGUAGE_TYPE", "CHN_ENG")
OCR_DETECT_DIRECTION = os.getenv("BAIDU_OCR_DETECT_DIRECTION", "true").lower() == "true"

_token_cache = {"token": None, "expire_at": 0}
MAX_OCR_SIDE = int(os.getenv("BAIDU_OCR_MAX_SIDE", "2000"))


def get_access_token() -> str:
    """获取百度 access_token，带缓存，过期前自动刷新。"""
    api_key = os.getenv("BAIDU_API_KEY", "").strip()
    secret_key = os.getenv("BAIDU_SECRET_KEY", "").strip()
    if not api_key or "在这里填" in api_key:
        raise RuntimeError("未配置 BAIDU_API_KEY：请在 .env 中填写百度智能云应用的 API Key")
    if not secret_key or "在这里填" in secret_key:
        raise RuntimeError("未配置 BAIDU_SECRET_KEY：请在 .env 中填写百度智能云应用的 Secret Key")

    now = time.time()
    if _token_cache["token"] and now < _token_cache["expire_at"] - 600:
        return _token_cache["token"]

    params = {
        "grant_type": "client_credentials",
        "client_id": api_key,
        "client_secret": secret_key,
    }
    resp = requests.get(TOKEN_URL, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _token_cache["token"] = data["access_token"]
    _token_cache["expire_at"] = now + data.get("expires_in", 2592000)  # 30 天
    return _token_cache["token"]


def ocr_image(image_bytes: bytes) -> str:
    """传入图片字节，返回识别出的文字（按行拼接）。"""
    image_bytes = _prepare_for_ocr(image_bytes)
    params = {"access_token": get_access_token()}
    payload = {
        "image": base64.b64encode(image_bytes).decode("utf-8"),
        "language_type": OCR_LANGUAGE,
        "detect_direction": "true" if OCR_DETECT_DIRECTION else "false",
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    resp = requests.post(OCR_URL, params=params, data=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    if "error_code" in data:
        raise RuntimeError(
            f"百度OCR错误 {data.get('error_code')}: {data.get('error_msg')}"
        )

    words = [item["words"] for item in data.get("words_result", [])]
    return "\n".join(words)


def _prepare_for_ocr(data: bytes) -> bytes:
    """百度 OCR 对图片大小有限制（过大报 216205 input oversize）：
    先转 RGB、最长边压到 MAX_OCR_SIDE、存为 JPEG，再送识别。"""
    img = Image.open(io.BytesIO(data)).convert("RGB")
    if max(img.size) > MAX_OCR_SIDE:
        img.thumbnail((MAX_OCR_SIDE, MAX_OCR_SIDE))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    return buf.getvalue()
