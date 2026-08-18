import datetime
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "uploads"))
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "10"))
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def save_image(data: bytes, filename: str) -> str:
    """保存图片到 uploads/年/月/日/uuid.扩展名，返回相对路径。"""
    ext = Path(filename or "upload.jpg").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"不支持的图片格式: {ext}")
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise ValueError(f"图片超过 {MAX_FILE_MB}MB 上限")

    today = datetime.date.today()
    folder = STORAGE_DIR / f"{today.year:04d}" / f"{today.month:02d}" / f"{today.day:02d}"
    folder.mkdir(parents=True, exist_ok=True)

    new_name = uuid.uuid4().hex + ext
    path = folder / new_name
    path.write_bytes(data)
    return path.as_posix()  # 统一用 / 分隔，跨平台一致


def delete_image(relative_path: str):
    """删除图片（隐私要求"不存原图"时用）。"""
    p = Path(relative_path)
    if p.exists():
        p.unlink()
