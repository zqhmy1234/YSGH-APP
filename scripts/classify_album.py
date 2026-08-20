"""把 test_album/ 平铺照片自动整理为 7 类子目录。

分类优先级：重复副本 → 模糊/敏感 → 含文字 → 纯图像 → 相似组 → 连续/间隔拍摄。
输出 test_album/classify_report.csv（filename, category, note）供人工抽查。
"""

import csv
import hashlib
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import sensitive_guard
from clip_service import embed_image, embed_text

ALBUM = Path("test_album")
CATEGORIES = [
    "01_连续拍摄",
    "02_间隔拍摄",
    "03_相似组",
    "04_重复副本",
    "05_含文字",
    "06_纯图像",
    "07_边界敏感",
]
ALLOWED = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}

TEXT_PROMPTS = [
    "包含文字的图片",
    "文档或截图",
    "发票、名片、菜单、书籍封面",
]
NATURAL_PROMPTS = [
    "风景照片",
    "人物照片",
    "食物照片",
    "动物照片",
    "日常物品照片",
]

BLUR_THRESHOLD = 30.0
SIM_PAIR_THRESHOLD = 0.9
BURST_GAP_MINUTES = 10

CATEGORY_DIRS = {ALBUM / c for c in CATEGORIES}


def blur_variance(img) -> float:
    gray = img.convert("L")
    edges = np.asarray(gray.filter(ImageFilter.FIND_EDGES), dtype=float)
    return float(edges.var())


def time_from_name(path) -> datetime | None:
    name = path.stem
    import re

    m = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})[-_]?(\d{2})[-_]?(\d{2})[-_]?(\d{2})", name)
    if m:
        try:
            return datetime(*map(int, m.groups()))
        except ValueError:
            pass
    m = re.search(r"(1[6-9]\d{12})", name)
    if m:
        try:
            return datetime.fromtimestamp(int(m.group(1)) / 1000)
        except (ValueError, OSError):
            pass
    return None


def main():
    if "--reset" in sys.argv:
        for d in CATEGORY_DIRS:
            if d.exists():
                for p in d.iterdir():
                    if p.is_file():
                        p.replace(ALBUM / p.name)
                d.rmdir()
        report = ALBUM / "classify_report.csv"
        if report.exists():
            report.unlink()
        print("已重置：照片移回平铺目录")
        return

    for c in CATEGORIES:
        (ALBUM / c).mkdir(parents=True, exist_ok=True)

    files = [
        p
        for p in ALBUM.iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED
    ]
    print(f"扫描到 {len(files)} 张平铺照片")

    text_vecs = [np.array(embed_text(p), dtype=float) for p in TEXT_PROMPTS]
    text_vecs = np.array(text_vecs)
    text_vecs = text_vecs / (np.linalg.norm(text_vecs, axis=1, keepdims=True) + 1e-12)
    natural_vecs = [np.array(embed_text(p), dtype=float) for p in NATURAL_PROMPTS]
    natural_vecs = np.array(natural_vecs)
    natural_vecs = natural_vecs / (np.linalg.norm(natural_vecs, axis=1, keepdims=True) + 1e-12)

    records = []  # (path, category, note, time, vec)
    seen_hash = {}

    for p in files:
        data = p.read_bytes()
        h = hashlib.sha256(data).hexdigest()
        vec = np.array(embed_image(data), dtype=float)
        vec = vec / (np.linalg.norm(vec) + 1e-12)
        note = ""

        # 1) 重复副本
        if h in seen_hash:
            records.append([p, "04_重复副本", f"与 {seen_hash[h]} 相同", None, vec])
            continue
        seen_hash[h] = p.name

        # 2) 模糊 / 敏感
        if blur_variance(Image.open(p)) < BLUR_THRESHOLD:
            records.append([p, "07_边界敏感", "模糊(低清晰度)", None, vec])
            continue
        guard = sensitive_guard.check_sensitive(vec.tolist())
        if guard["is_sensitive"]:
            records.append([p, "07_边界敏感", f"敏感({guard['matched']})", None, vec])
            continue

        # 3) 含文字 vs 纯图像
        s_text = float((text_vecs @ vec).max())
        s_natural = float((natural_vecs @ vec).max())
        t = time_from_name(p)
        if s_text > s_natural:
            records.append([p, "05_含文字", f"text={s_text:.2f}/natural={s_natural:.2f}", t, vec])
        else:
            records.append([p, "06_纯图像", f"natural={s_natural:.2f}/text={s_text:.2f}", t, vec])

    # 4) 相似组：纯图像中余弦 ≥ 阈值的配对，后出现者移入 03
    natural = [r for r in records if r[1] == "06_纯图像"]
    moved_sim = set()
    for i in range(len(natural)):
        for j in range(i + 1, len(natural)):
            if natural[i][0] in moved_sim or natural[j][0] in moved_sim:
                continue
            if float(natural[i][4] @ natural[j][4]) >= SIM_PAIR_THRESHOLD:
                natural[j][1] = "03_相似组"
                natural[j][2] = f"与 {natural[i][0].name} 相似"
                moved_sim.add(natural[j][0])

    # 5) 连续/间隔：剩余纯图像按时间分组
    rest = [r for r in natural if r[1] == "06_纯图像" and r[3] is not None]
    rest.sort(key=lambda r: r[3])
    for idx, r in enumerate(rest):
        if idx == 0:
            continue
        gap = (r[3] - rest[idx - 1][3]).total_seconds() / 60.0
        if gap <= BURST_GAP_MINUTES:
            r[1] = "01_连续拍摄"
            r[2] = f"与上一张间隔 {gap:.0f} 分钟"
        else:
            r[1] = "02_间隔拍摄"
            r[2] = f"与上一张间隔 {gap:.0f} 分钟"
    if rest:
        rest[0][1] = "01_连续拍摄" if len(rest) > 1 and (rest[1][3] - rest[0][3]).total_seconds() / 60 <= BURST_GAP_MINUTES else "02_间隔拍摄"

    # 移动文件并输出报告
    rows = []
    counts = {c: 0 for c in CATEGORIES}
    for p, cat, note, t, vec in records:
        dest = ALBUM / cat / p.name
        if p.resolve() != dest.resolve():
            p.replace(dest)
        rows.append({"filename": dest.name, "category": cat, "note": note})
        counts[cat] += 1

    with open(ALBUM / "classify_report.csv", "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["filename", "category", "note"])
        writer.writeheader()
        writer.writerows(rows)

    print("分类结果：")
    for c in CATEGORIES:
        print(f"  {c}: {counts[c]}")
    print(f"报告已写入 {ALBUM / 'classify_report.csv'}")


if __name__ == "__main__":
    main()
