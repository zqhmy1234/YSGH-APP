#!/usr/bin/env python3
"""忆述光华 · 客户端第一波验收：生成测试照片集（B-VA-1 · 2026-08-24）

生成 50 张带 EXIF 拍摄时间的照片（Pillow），模拟 3 天生活片段：
  - Day1（T-2）：20 张 = 上午在家 10 张 + 下午公园 10 张（L2 双主题）
  - Day2（T-1）：15 张 = 晚间在家 15 张（L2 单主题）
  - Day3（T-0）：15 张 = 上午公园 15 张（L2 单主题）
→ 期望 L1 日卡片 3 张；L2 主题 4 个（Day1 两个 / Day2 一个 / Day3 一个）。

用法：
  python scripts/generate_test_photos.py            # 仅生成（默认 .cowork-temp/test_photos/）
  python scripts/generate_test_photos.py --push     # 生成 + adb push + MEDIA_SCANNER 广播（需真机已授权）

说明：adb push 后通过广播触发 MediaScanner 扫描，App 端 ContentObserver 才会收到变更回调。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / ".cowork-temp" / "test_photos"

# 拍摄片段定义：(张数, 拍摄起点, 每张间隔分钟, (r,g,b) 底色, 图形类型)
# 底色/图形变化保证 50 张视觉可区分（便于核对上传顺序与事件归属）
SEGMENTS: list[tuple[int, datetime, int, tuple[int, int, int], str]] = [
    # Day1 上午·家
    (10, datetime(2026, 8, 22, 8, 0), 6, (176, 140, 110), "circle"),
    # Day1 下午·公园
    (10, datetime(2026, 8, 22, 15, 0), 9, (120, 150, 90), "rect"),
    # Day2 晚间·家
    (15, datetime(2026, 8, 23, 18, 0), 4, (150, 120, 140), "triangle"),
    # Day3 上午·公园
    (15, datetime(2026, 8, 24, 10, 0), 4, (170, 130, 70), "circle"),
]

WIDTH, HEIGHT = 1024, 768


def _exif_bytes(dt: datetime) -> bytes:
    """写 DateTimeOriginal/DateTimeDigitized（Pillow Exif 对象，无 piexif 依赖）"""
    exif = Image.Exif()
    ts = dt.strftime("%Y:%m:%d %H:%M:%S")
    exif[36867] = ts  # DateTimeOriginal
    exif[36868] = ts  # DateTimeDigitized
    return exif.tobytes()


def _draw(draw: ImageDraw.ImageDraw, kind: str, seed: int) -> None:
    import random

    rng = random.Random(seed)
    if kind == "circle":
        cx, cy, r = rng.randint(150, 870), rng.randint(150, 600), rng.randint(60, 160)
        draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(90, 70, 50))
    elif kind == "rect":
        x0, y0 = rng.randint(100, 700), rng.randint(100, 500)
        x1, y1 = x0 + rng.randint(120, 280), y0 + rng.randint(120, 280)
        draw.rectangle((x0, y0, x1, y1), fill=(90, 70, 50))
    else:  # triangle
        cx, cy, r = rng.randint(150, 870), rng.randint(150, 600), rng.randint(80, 180)
        draw.polygon([(cx, cy - r), (cx - r, cy + r), (cx + r, cy + r)], fill=(90, 70, 50))


def generate(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    idx = 0
    for count, start, step_min, base_color, kind in SEGMENTS:
        for i in range(count):
            taken = start + timedelta(minutes=step_min * i)
            img = Image.new("RGB", (WIDTH, HEIGHT), base_color)
            _draw(ImageDraw.Draw(img), kind, idx)
            name = f"test_{idx + 1:02d}_{taken.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.jpg"
            path = out_dir / name
            img.save(path, "JPEG", quality=88, exif=_exif_bytes(taken))
            files.append(path)
            idx += 1
    return files


def push_and_scan(files: list[Path]) -> None:
    """adb push 到 /sdcard/Pictures/yishu_test + MediaScanner 广播注入（POC-01 路径）"""
    device_dir = "/sdcard/Pictures/yishu_test"
    try:
        subprocess.run(["adb", "shell", "mkdir", "-p", device_dir], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"[push] adb 不可用或设备未连接：{exc}")
        print("[push] 请连接并授权 nova 11（USB 调试 → 允许）后重跑 --push")
        return
    for f in files:
        subprocess.run(["adb", "push", str(f), device_dir], check=False, capture_output=True)
    subprocess.run(
        ["adb", "shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
         "-d", f"file://{device_dir}"],
        check=False,
    )
    # 逐文件广播（部分厂商对目录广播支持不全）
    for f in files:
        subprocess.run(
            ["adb", "shell", "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
             "-d", f"file://{device_dir}/{f.name}"],
            check=False, capture_output=True,
        )
    print(f"[push] 已推送 {len(files)} 张并触发 MediaScanner 广播")


def main() -> int:
    parser = argparse.ArgumentParser(description="生成客户端第一波验收测试照片集（B-VA-1）")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出目录")
    parser.add_argument("--push", action="store_true", help="生成后 adb push + MediaScanner 广播")
    args = parser.parse_args()

    files = generate(args.out)
    print(f"[gen] 生成 {len(files)} 张测试照片 → {args.out}")
    print("[gen] 期望真值：L1 日卡片 3 张（08-22/08-23/08-24），L2 主题 4 个")

    if args.push:
        push_and_scan(files)
    return 0


if __name__ == "__main__":
    sys.exit(main())
