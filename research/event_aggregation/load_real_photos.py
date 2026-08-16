"""真实照片基准加载器（替代合成生成器）

用户指令（2026-08-16）：M1 Part 1 的 500 张测试照片基准改用真实截图
（C:\\Users\\ghf\\Pictures\\Screenshots），不用生成器合成。

- 截图命名编码真实时间戳：`屏幕截图 YYYY-MM-DD HHMMSS.png`
- 截图无 GPS → 走 B3 矩阵 #6（无 GPS：按时间窗归组）路径，真实数据验证
- 抽样策略：按月分层比例 + 单日配额上限（防单日爆发主导），目标 500 张

用法：
    python -m research.event_aggregation.load_real_photos   # 打印抽样统计
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from .pipeline import RawPhoto

SCREENSHOT_DIR = Path(r"C:\Users\ghf\Pictures\Screenshots")
_NAME_RE = re.compile(r"^屏幕截图 (\d{4}-\d{2}-\d{2}) (\d{6})\.png$")

DEFAULT_TARGET = 500
DEFAULT_MAX_PER_DAY = 12  # 单日配额：截图中单日最多 116 张，配额防主导
BURST_WINDOW_MIN = 90     # 日内取"最早爆发段"：首张起 90 分钟内（保留真实连拍结构）


def load_screenshots(directory: Path = SCREENSHOT_DIR) -> list[RawPhoto]:
    """扫描截图目录 → RawPhoto 列表（按时间升序，无 GPS，source="screenshot"）"""
    photos: list[RawPhoto] = []
    for f in sorted(directory.glob("*.png")):
        m = _NAME_RE.match(f.name)
        if not m:
            continue
        ts = datetime.strptime(f"{m.group(1)} {m.group(2)}", "%Y-%m-%d %H%M%S")  # noqa: DTZ007 —— 管线全链用本地朴素时间（与 generate_test_photos 一致，无时区字段）
        photos.append(
            RawPhoto(
                id=f"shot-{f.stem}",
                ts=ts,
                lat=None,
                lng=None,
                source="screenshot",
            )
        )
    photos.sort(key=lambda p: p.ts)
    return photos


def sample_500(
    photos: list[RawPhoto],
    target: int = DEFAULT_TARGET,
    max_per_day: int = DEFAULT_MAX_PER_DAY,
) -> list[RawPhoto]:
    """按月分层抽样至 ~target 张（保持真实月度分布，单日不超配额）

    1. 按自然月分组，按占比分配配额（不足的月补到别的月）
    2. 月内按天均匀取（每天最多 max_per_day 张，从每天开头取保持时间跨度）
    3. 凑满 target；超出则从尾部截断
    """
    if len(photos) <= target:
        return photos

    by_month: dict[str, list[RawPhoto]] = defaultdict(list)
    for p in photos:
        by_month[p.ts.strftime("%Y-%m")].append(p)

    # 按月比例分配基础配额
    total = len(photos)
    quota: dict[str, int] = {}
    assigned = 0
    for month, ps in sorted(by_month.items()):
        q = max(1, round(len(ps) / total * target))
        quota[month] = q
        assigned += q

    # 配额总和可能 ≠ target：多退少补（从最大的月调）
    if assigned < target:
        # 从单月内配额已满（>max_per_day）的月补
        shortage = target - assigned
        for month, _q in sorted(quota.items(), key=lambda kv: kv[1], reverse=True):
            if shortage <= 0:
                break
            quota[month] += 1
            shortage -= 1
    elif assigned > target:
        excess = assigned - target
        for month, q in sorted(quota.items(), key=lambda kv: kv[1]):
            if excess <= 0:
                break
            take = min(q - 1, excess)
            quota[month] -= take
            excess -= take

    # 月内按天取：每天取"最早爆发段"（首张起 BURST_WINDOW_MIN 分钟内，最多 max_per_day）
    # 保留真实连拍结构（截图常成簇出现）→ L0 时间窗聚类可被真实数据触发
    picked: list[RawPhoto] = []
    for month, q in sorted(quota.items()):
        by_day: dict[str, list[RawPhoto]] = defaultdict(list)
        for p in by_month[month]:
            by_day[p.ts.date().isoformat()].append(p)
        month_pick: list[RawPhoto] = []
        for d in sorted(by_day):
            day_photos = by_day[d]
            if not day_photos:
                continue
            first = day_photos[0].ts
            window_end = first + __import__("datetime").timedelta(minutes=BURST_WINDOW_MIN)
            burst = [p for p in day_photos if p.ts <= window_end][:max_per_day]
            month_pick.extend(burst)
            if len(month_pick) >= q:
                break
        month_pick.sort(key=lambda p: p.ts)
        picked.extend(month_pick[:q])  # 超配额截断（防止吞掉后续月份）

    picked.sort(key=lambda p: p.ts)
    return picked[:target]


def _print_stats(photos: list[RawPhoto]) -> None:
    if hasattr(__import__("sys").stdout, "reconfigure"):
        __import__("sys").stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"总张数: {len(photos)}")
    print(f"时间范围: {photos[0].ts:%Y-%m-%d %H:%M} ~ {photos[-1].ts:%Y-%m-%d %H:%M}")
    months = Counter(p.ts.strftime("%Y-%m") for p in photos)
    days = Counter(p.ts.date().isoformat() for p in photos)
    print(f"覆盖月数: {len(months)} | 覆盖天数: {len(days)}")
    print(f"单日最多: {max(days.values())} 张 | 日均: {len(photos) / len(days):.1f}")
    print("按月分布:")
    for m, c in sorted(months.items()):
        print(f"  {m}: {c}")


if __name__ == "__main__":
    all_photos = load_screenshots()
    print(f"扫描到 {len(all_photos)} 张截图")
    picked = sample_500(all_photos)
    _print_stats(picked)
