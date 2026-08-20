"""RAG 图片检索基准 · 语料构建（M1 Part 2 门禁 Top3≥70% 的前置基建）

从 C:\\Users\\ghf\\Pictures\\Screenshots（3078 张真实截图）抽样 500 张，
生成 corpus.json：{id, path, taken_at, filename}。
图片塔（Qwen3-VL）拿到 DASHSCOPE key 后，run_eval.py 对语料打描述 → 索引 → 评估。

用法：
    python -m research.rag_benchmark.build_corpus [--target 500] [--out corpus.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCREENSHOT_DIR = Path(r"<LOCAL_SCREENSHOTS_DIR>")
OUT_DEFAULT = Path(__file__).resolve().parent / "corpus.json"
_NAME_RE = re.compile(r"^屏幕截图 (\d{4}-\d{2}-\d{2}) (\d{6})\.png$")


def scan() -> list[dict]:
    """扫描截图 → [{id, path, taken_at, filename}]（时间升序）"""
    items: list[dict] = []
    for f in sorted(SCREENSHOT_DIR.glob("*.png")):
        m = _NAME_RE.match(f.name)
        if not m:
            continue
        ts = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H%M%S")  # noqa: DTZ007 —— 截图文件名编码本地时间，无时区字段
        items.append(
            {
                "id": f"img-{m.group(1).replace('-', '')}-{m.group(2)}",
                "path": str(f),
                "taken_at": ts.isoformat(),
                "filename": f.name,
            }
        )
    return items


def sample_500(items: list[dict], target: int = 500, max_per_day: int = 12) -> list[dict]:
    """按月分层 + 日内取最早段（与事件聚合基准同策略，保证月度分布真实）"""
    if len(items) <= target:
        return items
    by_month: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        by_month[it["taken_at"][:7]].append(it)

    quota: dict[str, int] = {}
    assigned = 0
    for month, its in sorted(by_month.items()):
        q = max(1, round(len(its) / len(items) * target))
        quota[month] = q
        assigned += q
    if assigned < target:
        for month, _q in sorted(quota.items(), key=lambda kv: kv[1], reverse=True):
            if assigned >= target:
                break
            quota[month] += 1
            assigned += 1
    elif assigned > target:
        # 修复（审查 MINOR）：q=1 时 min(q-1, ...)=0 扣不动 → 跳过（配额下限 1）
        for month, q in sorted(quota.items(), key=lambda kv: kv[1]):
            if assigned <= target or q <= 1:
                continue
            take = min(q - 1, assigned - target)
            quota[month] -= take
            assigned -= take

    picked: list[dict] = []
    for month, q in sorted(quota.items()):
        by_day: dict[str, list[dict]] = defaultdict(list)
        for it in by_month[month]:
            by_day[it["taken_at"][:10]].append(it)
        month_pick: list[dict] = []
        for d in sorted(by_day):
            day_items = by_day[d]
            first = datetime.fromisoformat(day_items[0]["taken_at"])
            window_end = first + __import__("datetime").timedelta(minutes=90)  # 最早 90 分钟段
            burst = [x for x in day_items if datetime.fromisoformat(x["taken_at"]) <= window_end][:max_per_day]
            month_pick.extend(burst)
            if len(month_pick) >= q:
                break
        picked.extend(month_pick[:q])

    picked.sort(key=lambda x: x["taken_at"])
    return picked[:target]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=500)
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    args = parser.parse_args()

    items = scan()
    picked = sample_500(items, target=args.target)
    data = {
        "_meta": {
            "version": 1,
            "source": str(SCREENSHOT_DIR),
            "total_available": len(items),
            "target": args.target,
            "note": "真实截图（无 GPS）；图片塔就绪后 run_eval.py 打描述并评估 Top3",
        },
        "items": picked,
    }
    args.out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    months = len({x["taken_at"][:7] for x in picked})
    print(f"语料已生成: {args.out}")
    print(f"共 {len(picked)} 张 | 覆盖 {months} 个月 | {picked[0]['taken_at'][:10]} ~ {picked[-1]['taken_at'][:10]}")


if __name__ == "__main__":
    main()
