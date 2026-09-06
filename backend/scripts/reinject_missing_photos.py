"""
reinject_missing_photos.py —— 照片原件缺失补注入（2026-09-04 峰宝拍板）
背景：3c2a2342 用户 20 张照片原件历史缺失（DB 行+事件挂载在，盘上文件无）。
方案：从 C:/Users/ghf/Pictures/Screenshots 随机抽取（排除 seed_demo 已用的 40 张），
     按原 cos_key 路径落盘（零 DB 结构改动）+ generate_thumbnail 重生成缩略图。
用法：cd .wt/missing-pages/backend && FS_STORAGE_ROOT=D:/GuangH-App/backend/data/storage \
     python scripts/reinject_missing_photos.py [--user 3c2a2342] [--dry-run]
"""
# ruff: noqa: E402, S311, B007  （E402=sys.path/chdir 后才能 import；S311=非加密随机抽图；B007=核验循环）
import argparse
import io
import os
import random
import sys

_BACKEND = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, _BACKEND)
os.chdir(_BACKEND)

from app.db.session import SessionLocal
from app.services.thumbnails import generate_thumbnail
from PIL import Image
from sqlalchemy import text

SRC_DIR = r"C:\Users\ghf\Pictures\Screenshots"
USER_PREFIX = "3c2a2342"
SEED_DEMO_SEED = 20260831   # seed_demo.py 同种子：复算它用掉的 40 张，避免撞图
SEED_DEMO_COUNT = 40


def to_jpeg_bytes(src: str) -> bytes:
    img = Image.open(src)
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((1600, 1600), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue()


def used_by_seed_demo() -> set:
    """复算 seed_demo.py（random.seed(20260831) 后 shuffle 取前 40）用掉的图，排除防撞。"""
    pngs = [f for f in os.listdir(SRC_DIR) if f.lower().endswith(".png")]
    rng = random.Random(SEED_DEMO_SEED)
    rng.shuffle(pngs)
    return set(pngs[:SEED_DEMO_COUNT])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default=USER_PREFIX, help="用户 UID 前缀")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    storage_root = os.environ.get("FS_STORAGE_ROOT")
    if not storage_root:
        print("❌ 必须显式传 FS_STORAGE_ROOT（运行中服务的存储根），避免 cwd 相对路径落错盘")
        sys.exit(1)

    db = SessionLocal()
    row = db.execute(text(
        "SELECT id FROM users WHERE id::text LIKE :p"
    ), {"p": args.user + "%"}).fetchone()
    if not row:
        print(f"❌ 找不到用户前缀 {args.user}")
        sys.exit(1)
    uid = str(row[0])

    photos = db.execute(text(
        "SELECT id, cos_key FROM contents WHERE user_id=:u AND content_type='photo' "
        "AND deleted_at IS NULL ORDER BY taken_at"
    ), {"u": uid}).fetchall()

    targets = []
    for pid, ck in photos:
        if ck and os.path.isfile(os.path.join(storage_root, ck)):
            continue  # 原件在盘，跳过
        targets.append((str(pid), ck))
    print(f"[pool] 用户 {uid} 照片 {len(photos)} 张，原件缺失 {len(targets)} 张")

    if not targets:
        print("[done] 无需补图")
        return

    exclude = used_by_seed_demo()
    pngs = [f for f in os.listdir(SRC_DIR) if f.lower().endswith(".png") and f not in exclude]
    rng = random.Random()
    rng.shuffle(pngs)
    if len(pngs) < len(targets):
        print(f"❌ 素材不足：可用 {len(pngs)} < 需 {len(targets)}")
        sys.exit(1)
    picks = pngs[:len(targets)]
    print(f"[pick] Screenshots 可用 {len(pngs)} 张（已排除 seed_demo 40 张），抽 {len(picks)} 张")

    ok, fail = 0, 0
    for (pid, ck), fname in zip(targets, picks):
        src = os.path.join(SRC_DIR, fname)
        dest = os.path.join(storage_root, ck)
        if args.dry_run:
            print(f"  [dry] {fname} -> {ck}")
            continue
        try:
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "wb") as f:
                f.write(to_jpeg_bytes(src))
        except Exception as exc:
            print(f"  [fail] {fname}: {exc}")
            fail += 1
            continue
        r = generate_thumbnail(db, pid)
        if r.get("status") == "created":
            ok += 1
            print(f"  [ok] {fname} -> {ck} + 缩略图")
        else:
            print(f"  [warn] 原件已落盘但缩略图异常: {r}")
            ok += 1
    if not args.dry_run:
        db.commit()

    # 终态核验
    n_orig = n_thumb = 0
    for pid, ck in photos:
        if ck and os.path.isfile(os.path.join(storage_root, ck)):
            n_orig += 1
    rows = db.execute(text(
        "SELECT coalesce(thumbnail_key,''), cos_key FROM contents "
        "WHERE user_id=:u AND content_type='photo' AND deleted_at IS NULL"
    ), {"u": uid}).fetchall()
    for tk, ck in rows:
        if tk and os.path.isfile(os.path.join(storage_root, tk)):
            n_thumb += 1
    print(f"[verify] 原件在盘 {n_orig}/{len(photos)}  缩略图在盘 {n_thumb}/{len(photos)}  本次失败 {fail}")
    db.close()


if __name__ == "__main__":
    main()
