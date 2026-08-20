import datetime
import io
import os
import uuid
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image

load_dotenv()

STORAGE_DIR = Path(os.getenv("STORAGE_DIR", "uploads"))
THUMB_DIR = Path(os.getenv("THUMB_DIR", "thumbnails"))
MAX_FILE_MB = int(os.getenv("MAX_FILE_MB", "10"))
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def validate(data: bytes, filename: str):
    """只校验格式与大小，不写盘（用于入库前快速过滤）。"""
    ext = Path(filename or "upload.jpg").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"不支持的图片格式: {ext}")
    if len(data) > MAX_FILE_MB * 1024 * 1024:
        raise ValueError(f"图片超过 {MAX_FILE_MB}MB 上限")


def get_exif_datetime(data: bytes):
    """从图片 EXIF 提取拍摄时间（DateTimeOriginal → DateTime → DateTimeDigitized），无则返回 None。"""
    try:
        from PIL import ExifTags

        img = Image.open(io.BytesIO(data))
        exif = img.getexif()
        if exif:
            wanted = {"DateTimeOriginal", "DateTime", "DateTimeDigitized"}
            values = {}
            for tag, value in exif.items():
                name = ExifTags.TAGS.get(tag)
                if name in wanted and value:
                    values[name] = str(value)
            for name in ("DateTimeOriginal", "DateTime", "DateTimeDigitized"):
                if name in values:
                    return values[name].replace(":", "-", 2)
    except Exception:
        pass
    return None


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


def save_thumbnail(data: bytes) -> str:
    """原图 → 512px JPEG 缩略图，返回相对路径（KEEP_ORIGINAL=false 时用于检索/展示）。"""
    max_size = int(os.getenv("THUMB_MAX_SIZE", "512"))
    quality = int(os.getenv("THUMB_QUALITY", "85"))

    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((max_size, max_size))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)

    today = datetime.date.today()
    folder = THUMB_DIR / f"{today.year:04d}" / f"{today.month:02d}" / f"{today.day:02d}"
    folder.mkdir(parents=True, exist_ok=True)

    path = folder / (uuid.uuid4().hex + ".jpg")
    path.write_bytes(buf.getvalue())
    return path.as_posix()


def delete_image(relative_path: str):
    """删除图片（隐私要求"不存原图"时用）。"""
    p = Path(relative_path)
    if p.exists():
        p.unlink()
