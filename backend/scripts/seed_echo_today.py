"""
seed_echo_today.py —— 去年今日回响数据注入（2026-09-05 峰宝拍板「注入数据」）
背景：消息中心回声卡走 GET /echo/today（取去年今日 taken_at 的内容），
     真机账号无 2025-09-05 内容 → 回响空 → USE_MOCK_ECHO 演示卡点击假 id → detail 404。
     正解=注入真数据后关演示开关。
方案：从 C:/Users/ghf/Pictures/Screenshots 随机抽 6 张（全局 md5 防撞已有 cos_key），
     落盘 fs 存储 + contents 行（taken_at=去年今日 09:00-14:00 本地错开）+ 缩略图生成，
     再跑 run_user_aggregation(mode="full") 把新照片挂上事件（timeline 一致）。
用法：cd .wt/missing-pages/backend && FS_STORAGE_ROOT=D:/GuangH-App/backend/data/storage \
     python scripts/seed_echo_today.py [--user 7e4b6a2a] [--count 6] [--dry-run]
"""
# ruff: noqa: E402, S311, S324, E501
# (S324=md5 for asset fingerprint only, S311=random pick, E402=chdir imports, E501=long query lines)  （E402=sys.path/chdir 后才能 import；S311=非加密随机抽图）
import argparse
import hashlib
import io
import os
import random
import sys
from datetime import datetime, timedelta, timezone

_BACKEND = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, _BACKEND)
os.chdir(_BACKEND)

from app.db.session import SessionLocal
from app.services.thumbnails import generate_thumbnail
from PIL import Image
from sqlalchemy import text

SRC_DIR = r"C:\Users\ghf\Pictures\Screenshots"
USER_PREFIX = "7e4b6a2a"
ECHO_DATE = "2025-09-05"   # 去年今日默认值（echo 域按本地日界查询）；R7 起可用 --date 覆盖——
# 教训（2026-09-06）：日期硬编码导致跨天后「去年今日」无数据，回声卡空转一整天
PREFIX = "photos"


def to_jpeg_bytes(src: str) -> bytes:
    img = Image.open(src)
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((1600, 1600), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85, optimize=True)
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--user", default=USER_PREFIX, help="用户 UID 前缀")
    ap.add_argument("--count", type=int, default=6)
    ap.add_argument("--date", default=None, help="去年今日日期 YYYY-MM-DD（默认 2025-09-05；跨天必须传当天）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    echo_date = args.date if args.date else ECHO_DATE

    storage_root = os.environ.get("FS_STORAGE_ROOT")
    if not storage_root:
        print("❌ 必须显式传 FS_STORAGE_ROOT（运行中服务的存储根），避免 cwd 相对路径落错盘")
        sys.exit(1)

    db = SessionLocal()
    row = db.execute(text("SELECT id FROM users WHERE id::text LIKE :p"), {"p": args.user + "%"}).fetchone()
    if not row:
        print(f"❌ 找不到用户前缀 {args.user}")
        sys.exit(1)
    uid = str(row[0])

    # 全局防撞：已有 cos_key 的 md5 前缀 + 盘上文件名指纹
    existing_keys = {r[0] for r in db.execute(text("SELECT cos_key FROM contents WHERE cos_key IS NOT NULL")).fetchall()}
    existing_hashes = {os.path.basename(k).split(".")[0] for k in existing_keys}

    pngs = [f for f in os.listdir(SRC_DIR) if f.lower().endswith(".png")]
    rng = random.Random()
    rng.shuffle(pngs)

    picks, seen = [], set()
    for fname in pngs:
        if len(picks) >= args.count:
            break
        with open(os.path.join(SRC_DIR, fname), "rb") as f:
            h = hashlib.md5(f.read()).hexdigest()[:16]
        if h in existing_hashes or h in seen:
            continue
        seen.add(h)
        picks.append((fname, h))
    if len(picks) < args.count:
        print(f"❌ 去重后素材不足：{len(picks)} < {args.count}")
        sys.exit(1)
    print(f"[pick] 抽 {len(picks)} 张（md5 全局防撞 {len(existing_hashes)} 已用）")

    y, m = echo_date.split("-")[0], echo_date.split("-")[1]
    ok = 0
    for i, (fname, h) in enumerate(picks):
        cos_key = f"{PREFIX}/{uid}/{y}{m}/{h}.jpg"
        thumb_key = f"thumbnails/{uid}/{y}{m}/{h}_tb.jpg"
        taken = datetime.fromisoformat(echo_date + f"T{9 + i:02d}:30:00").replace(tzinfo=timezone(timedelta(hours=8)))
        if args.dry_run:
            print(f"  [dry] {fname} -> {cos_key} taken_at={taken.isoformat()}")
            continue
        dest = os.path.join(storage_root, cos_key)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(to_jpeg_bytes(os.path.join(SRC_DIR, fname)))
        cid = db.execute(text(
            "INSERT INTO contents (id, user_id, content_type, status, cos_key, thumbnail_key, "
            "taken_at, source, created_at, updated_at) "
            # source='seed'（2026-09-06 峰宝拍板）：注入数据必须带结构化标记，
            # 聚合管线按 source!='seed' 排除——此前写 'app' 伪装成真实数据，
            # 导致 2025-09 照片混进今日语音事件（start_time=去年，时间轴沉底）
            "VALUES (gen_random_uuid(), :u, 'photo', 'ready', :ck, :tk, :ta, 'seed', now(), now()) "
            "RETURNING id"
        ), {"u": uid, "ck": cos_key, "tk": thumb_key, "ta": taken}).scalar()
        r = generate_thumbnail(db, cid)
        print(f"  [ok] {fname} -> {cos_key} (缩略图 {r.get('status')})")
        ok += 1
    if not args.dry_run:
        db.commit()

    # 挂事件（幂等聚合：新照片进 L1 日卡）
    if not args.dry_run and ok:
        from app.services.events.aggregate import run_user_aggregation
        run_user_aggregation(uid, mode="full")

    # 终态核验：echo 域口径查询
    d1 = (datetime.fromisoformat(echo_date + "T00:00:00+08").astimezone() + timedelta(days=1)).isoformat()
    n = db.execute(text(
        "SELECT count(*) FROM contents WHERE user_id=:u AND deleted_at IS NULL "
        "AND taken_at >= :d0 AND taken_at < :d1"
    ), {"u": uid, "d0": echo_date + "T00:00:00+08", "d1": d1}).scalar()
    print(f"[verify] 用户 {uid} 去年今日({echo_date})内容 {n} 条（echo 域可见）")
    db.close()


if __name__ == "__main__":
    main()
