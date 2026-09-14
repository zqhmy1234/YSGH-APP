"""205 张全链路验收脚本（阶段 3/4 · 2026-09-04，codex/photo-merge）

直连服务层 E2E：register_photo_content（pHash 去重 + files 落库）
→ process_content（OCR + BGE-M3 向量 + 状态回写）→ aggregate_user（事件聚合）
统计：总量/成功/去重/OCR 可用/事件数/耗时 P95
用法：python acceptance_import.py <照片目录> <user_uuid>
"""
import io
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

# 关键环境（须在 import app 前设置）
os.environ.setdefault("STORAGE_BACKEND", "minio")
os.environ.setdefault("MOCK_EXTERNAL_AI", "false")
os.environ.setdefault("PYTHONPATH", r"D:\projects\YSGH-APP\backend")
sys.path.insert(0, r"D:\projects\YSGH-APP\backend")

from PIL import Image  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.services.photo_content import register_photo_content  # noqa: E402
from app.services.pipeline import process_content  # noqa: E402
from app.services.upload_meta import PhotoMeta  # noqa: E402

SUPPORTED = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}


def exif_taken(data: bytes) -> datetime | None:
    """EXIF DateTimeOriginal（与 api/contents.py 同口径；无时区按本地 +08 解释）"""
    try:
        Image.MAX_IMAGE_PIXELS = 40_000_000
        img = Image.open(io.BytesIO(data))
        exif = img.getexif()
        raw = exif.get(36867)
        if not raw:
            return None
        dt = datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)  # 演示用 UTC 简化
    except Exception:
        return None


def collect_photos(root: Path) -> list[Path]:
    files = []
    for ext in SUPPORTED:
        files.extend(root.rglob("*" + ext))
        files.extend(root.rglob("*" + ext.upper()))
    return sorted(set(files))


def main() -> None:
    root = Path(sys.argv[1])
    user_id = sys.argv[2] if len(sys.argv) > 2 else str(uuid.uuid4())
    photos = collect_photos(root)
    print(f"照片总数: {len(photos)}", flush=True)

    db = SessionLocal()
    # 测试用户
    from app.db.models import User
    if db.get(User, user_id) is None:
        db.add(User(id=user_id))
        db.commit()
        print(f"已创建测试用户 {user_id}", flush=True)

    stats = {
        "total": len(photos), "registered": 0, "duplicate": 0,
        "ocr_nonempty": 0, "failed": 0, "errors": [],
        "durations": [],
    }
    content_ids: list[str] = []
    for i, path in enumerate(photos, 1):
        t0 = time.perf_counter()
        try:
            data = path.read_bytes()
            taken = exif_taken(data)
            ext = path.suffix.lower()
            cid = register_photo_content(
                db, user_id,
                dedup_key="perceptual_hash",
                moderate=False,
                mode="original",
                meta_obj={},
                photo_meta=PhotoMeta(taken_at=taken, gps_lat=None, gps_lng=None, source="import"),
                data=data,
                ext=ext,
                exif_taken_at=taken,
                enqueue_thumbnail=False,
            )
            stats["registered"] += 1
            content_ids.append(cid)
            try:
                process_content(cid)
            except Exception as exc:  # noqa: BLE001
                stats["errors"].append(f"{path.name}:pipeline:{type(exc).__name__}:{exc}")
        except Exception as exc:  # noqa: BLE001
            name = type(exc).__name__
            if name == "DuplicateError":
                stats["duplicate"] += 1
            else:
                stats["failed"] += 1
                stats["errors"].append(f"{path.name}:{name}:{exc}")
        stats["durations"].append(time.perf_counter() - t0)
        if i % 25 == 0 or i == len(photos):
            print(f"进度 {i}/{len(photos)}", flush=True)

    # 事件聚合（per-user 全量）
    try:
        from app.services.events import aggregate_user
        agg = aggregate_user(db, user_id)
        stats["events"] = agg
    except Exception as exc:  # noqa: BLE001
        stats["events_error"] = f"{type(exc).__name__}: {exc}"

    # 结果回查
    from sqlalchemy import func, select
    from app.db.models import Content, File
    rows = db.execute(select(Content)).scalars().all()
    stats["contents_rows"] = len(rows)
    stats["ocr_nonempty"] = sum(1 for r in rows if r.raw_text)
    stats["files_rows"] = db.execute(select(func.count()).select_from(File)).scalar_one()
    stats["phash_unique"] = len({r.image_phash for r in rows if r.image_phash})
    dur = sorted(stats["durations"])
    n = len(dur)
    stats["avg_sec"] = round(sum(dur) / n, 2) if n else 0
    stats["p95_sec"] = round(dur[int(n * 0.95) - 1], 2) if n else 0
    stats["max_sec"] = round(dur[-1], 2) if n else 0
    stats["error_count"] = len(stats["errors"])
    import json
    print("===== 验收汇总 =====")
    print(json.dumps(stats, ensure_ascii=False, indent=2, default=str))
    with open(r"D:\projects\photo_pipeline\_acceptance_result.json", "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2, default=str)
    db.close()


if __name__ == "__main__":
    main()