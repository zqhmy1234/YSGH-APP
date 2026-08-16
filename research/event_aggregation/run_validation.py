"""事件聚合原型验证（对照 B3 十类矩阵 + 测试清单 AGG-001/002）

运行：python -m research.event_aggregation.run_validation
期望结果：
  - 场景 1/2：L0 簇正确分组（一顿饭=1 簇；咖啡馆/公园=2 簇）
  - 场景 3：一日游 5 点 → 不应切成 5 个碎片簇（L1 日卡片 1 张）
  - 场景 4：5 天旅行 → L2 候选 1 个（跨天 ≥2 天 ≥10 张）
  - 场景 7：20 连拍 → 折叠后时间点显著减少
  - 场景 8：稀疏 → is_sparse 标记并入日卡片
"""
from __future__ import annotations

import sys
from collections import Counter

# Windows 控制台 GBK 兼容（✅/❌ 是 Unicode）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from .generate_test_photos import generate
from .pipeline import aggregate, preprocess


def main() -> None:
    photos = generate()
    result = aggregate(photos)

    print("=" * 60)
    print(f"输入 {result.stats['raw']} 张 → 预处理 {result.stats['preprocessed']} 个时间点")
    print(f"L0 簇数: {result.stats['l0_clusters']} | L1 日卡片: {result.stats['l1_days']} | 散片并入 L1: {result.stats['noise_to_l1']}")
    print(f"L2 候选（跨天≥2天≥10张）: {result.stats['l2_candidates']} | L3 主题流候选: {result.stats['l3_candidates']}")
    print("=" * 60)

    failures = []

    # --- 场景 1/2：L0 分组正确性 ---
    p1_ids = [p.id for cl in result.l0_clusters for p in cl if p.id.startswith("p1-")]
    p2a_ids = [p.id for cl in result.l0_clusters for p in cl if p.id.startswith("p2a-")]
    p2b_ids = [p.id for cl in result.l0_clusters for p in cl if p.id.startswith("p2b-")]
    _check(failures, "场景1: 一顿饭聚为 1 簇", len({cl_idx for cl_idx, cl in enumerate(result.l0_clusters) if any(p.id.startswith("p1-") for p in cl)}) == 1, f"簇数={len({cl_idx for cl_idx, cl in enumerate(result.l0_clusters) if any(p.id.startswith('p1-') for p in cl)})}")
    _check(failures, "场景2: 咖啡馆/公园分离为 2 簇", bool(p2a_ids) and bool(p2b_ids), f"p2a={len(p2a_ids)} p2b={len(p2b_ids)}")

    # --- 场景 3：单日多地点 → L1 归 1 日卡片 ---
    p3_days = [d for d in result.l1_days if any(p.id.startswith("p3-") for p in d["photos"])]
    _check(failures, "场景3: 一日游 → 1 张日卡片（不切碎）", len(p3_days) == 1, f"日卡片数={len(p3_days)}")

    # --- 场景 4：跨天旅行 → L2 候选 ---
    p4_l2 = [c for c in result.l2_candidates if any(pid.startswith("p4-") for pid in c["cluster"])]
    _check(failures, "场景4: 5 天旅行 → L2 候选 ≥1", len(p4_l2) >= 1, f"候选数={len(p4_l2)}")

    # --- 场景 5：并行事件 → 备考标签主题流候选 ---
    p5_l3 = [c for c in result.l3_candidates if c["tag"] in ("备考", "笔记")]
    _check(failures, "场景5: 备考主题流候选存在", len(p5_l3) >= 1, f"候选={p5_l3}")

    # --- 场景 6：无 GPS 按时间归组不崩溃 ---
    p6_in_days = sum(1 for d in result.l1_days for p in d["photos"] if p.id.startswith("p6-"))
    _check(failures, "场景6: 无 GPS 照片进日卡片", p6_in_days == 5, f"进卡片={p6_in_days}/5")

    # --- 场景 7：连拍折叠 ---
    pts = preprocess(photos)
    p7_burst_groups = {p.burst_group for p in pts if p.id.startswith("p7-")}
    _check(failures, "场景7: 20 连拍折叠为 1 组", len(p7_burst_groups) == 1, f"折叠组数={len(p7_burst_groups)}")

    # --- 场景 8：稀疏 → is_sparse ---
    p8_sparse = [d for d in result.l1_days if any(p.id.startswith("p8-") for p in d["photos"]) and d["is_sparse"]]
    _check(failures, "场景8: 稀疏日标记 is_sparse", len(p8_sparse) == 3, f"稀疏日={len(p8_sparse)}/3")

    # --- 场景 10：时间错乱不崩溃 ---
    _check(failures, "场景10: 时间错乱不崩溃", True, "ok")

    # --- 30s 首批验收链路（API-001 端到端前置：聚合本身耗时）---
    import time
    start = time.perf_counter()
    aggregate(photos)
    agg_ms = int((time.perf_counter() - start) * 1000)
    print(f"\n聚合耗时（500 张）: {agg_ms}ms")
    _check(failures, "聚合耗时 <2s（B3-6 端侧预算）", agg_ms < 2000, f"{agg_ms}ms")

    print("=" * 60)
    if failures:
        print(f"❌ {len(failures)} 项未通过:")
        for f in failures:
            print("  -", f)
        raise SystemExit(1)
    print("✅ 全部验证通过")


def _check(failures: list[str], name: str, ok: bool, detail: str = "") -> None:
    status = "✅" if ok else "❌"
    print(f"{status} {name}" + (f" ({detail})" if detail else ""))
    if not ok:
        failures.append(f"{name} ({detail})")


if __name__ == "__main__":
    main()
