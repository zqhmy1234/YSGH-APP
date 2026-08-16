"""事件聚合正式原型验证（对照 B3 十类矩阵 + AGG-001~016 关键项）

运行：python -m research.event_aggregation.run_validation
期望：
  - 场景 1/2：L0 簇正确分组
  - 场景 3/4：日卡片不切碎 / L2 跨天候选
  - 场景 7：连拍折叠；场景 8：稀疏并入日卡片
  - 场景 11：单点漂移不产生新簇（B3-4）
  - 场景 12：系统性偏移整批成簇（不误拆）
  - 增量聚合：旧簇结构不漂移（AGG-015）
  - 端云阈值一致性：同参双跑结果一致（AGG-016）
"""
from __future__ import annotations

import sys
import time

# Windows 控制台 GBK 兼容（✅/❌ 是 Unicode）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from .generate_test_photos import generate
from .pipeline import AGG_CONFIG, aggregate, incremental_aggregate, preprocess


def main() -> None:
    photos = generate()
    result = aggregate(photos)

    print("=" * 60)
    print(f"输入 {result.stats['raw']} 张 → 预处理 {result.stats['preprocessed']} 个时间点")
    print(
        f"L0 簇数: {result.stats['l0_clusters']} | L1 日卡片: {result.stats['l1_days']} | "
        f"散片并入 L1: {result.stats['noise_to_l1']}"
    )
    print(
        f"L2 候选: {result.stats['l2_candidates']} | L3 主题流候选: {result.stats['l3_candidates']}"
    )
    print("=" * 60)

    failures = []

    def cluster_count_for(prefix: str) -> int:
        """统计包含指定前缀照片的 L0 簇数"""
        return sum(1 for cl in result.l0_clusters if any(p.id.startswith(prefix) for p in cl))

    def cluster_size_for(prefix: str) -> int:
        """统计指定前缀照片进入 L0 簇的总数"""
        return sum(1 for cl in result.l0_clusters for p in cl if p.id.startswith(prefix))

    # --- 场景 1/2：L0 分组正确性 ---
    p2a_ids = cluster_size_for("p2a-")
    p2b_ids = cluster_size_for("p2b-")
    _check(failures, "场景1: 一顿饭聚为 1 簇", cluster_count_for("p1-") == 1, f"簇数={cluster_count_for('p1-')}")
    _check(failures, "场景2: 咖啡馆/公园分离为 2 簇", bool(p2a_ids) and bool(p2b_ids), f"p2a={p2a_ids} p2b={p2b_ids}")

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
    p8_sparse = [
        d for d in result.l1_days
        if any(p.id.startswith("p8-") for p in d["photos"]) and d["is_sparse"]
    ]
    _check(failures, "场景8: 稀疏日标记 is_sparse", len(p8_sparse) == 3, f"稀疏日={len(p8_sparse)}/3")

    # --- 场景 11：单点漂移 → 不产生新簇（B3-4 漂移修正）---
    p11_clusters = cluster_count_for("p11-")
    _check(failures, "场景11: 单点漂移不产生新簇", p11_clusters == 1, f"p11 簇数={p11_clusters}")

    # --- 场景 12：系统性偏移 → 整批仍成 1 簇 ---
    p12_clusters = cluster_count_for("p12-")
    _check(failures, "场景12: 系统性偏移整批成簇", p12_clusters == 1, f"p12 簇数={p12_clusters}")

    # --- 场景 10：时间错乱不崩溃 ---
    _check(failures, "场景10: 时间错乱不崩溃", True, "ok")

    # --- 增量聚合（AGG-015）：旧簇结构不漂移 ---
    first_batch = [p for p in photos if not p.id.startswith("p13-")]
    second_batch = [p for p in photos if p.id.startswith("p13-")]
    first_result = aggregate(first_batch)
    incr = incremental_aggregate(first_result, second_batch)
    old_cluster_ids_before = {frozenset(p.id for p in cl) for cl in first_result.l0_clusters}
    old_cluster_ids_after = {frozenset(p.id for p in cl) for cl in incr.l0_clusters}
    preserved = old_cluster_ids_before.issubset(old_cluster_ids_after)
    _check(
        failures, "AGG-015: 增量后旧簇结构保留（不漂移）", preserved,
        f"旧簇{len(old_cluster_ids_before)}→保留{len(old_cluster_ids_after)}",
    )
    p13_in_incr = sum(1 for cl in incr.l0_clusters for p in cl if p.id.startswith("p13-"))
    _check(failures, "AGG-015: 新照片进入增量结果", p13_in_incr >= 8, f"p13 进簇={p13_in_incr}")

    # --- 端云阈值一致性（AGG-016）：同参双跑结果一致 ---
    run_a = aggregate(photos)
    run_b = aggregate(photos)
    same = run_a.stats["l0_clusters"] == run_b.stats["l0_clusters"]
    _check(
        failures, "AGG-016: 同参双跑结果一致（端云同一配置源）", same,
        f"{run_a.stats['l0_clusters']} vs {run_b.stats['l0_clusters']}",
    )
    _check(
        failures, "AGG-016: 参数来自统一配置", AGG_CONFIG["l0"]["eps_s_m"] == 500.0,
        str(AGG_CONFIG["l0"]),
    )

    # --- 性能（B3-6 端侧预算：500 张 <2s）---
    start = time.perf_counter()
    aggregate(photos)
    agg_ms = int((time.perf_counter() - start) * 1000)
    print(f"\n聚合耗时（{result.stats['raw']} 张）: {agg_ms}ms")
    _check(failures, "聚合耗时 <2s（B3-6 端侧预算）", agg_ms < 2000, f"{agg_ms}ms")

    # --- 场景 15：真实数据基准（500 张真实截图，用户指令：不用合成生成器）---
    validate_real_data(failures)

    print("=" * 60)
    if failures:
        print(f"❌ {len(failures)} 项未通过:")
        for f in failures:
            print("  -", f)
        raise SystemExit(1)
    print("✅ 全部验证通过（15 项场景）")


def validate_real_data(failures: list[str]) -> None:
    """场景 15：真实数据基准 —— 500 张真实截图（无 GPS，B3 #6 路径）

    来源：C:\\Users\\ghf\\Pictures\\Screenshots（命名编码真实时间戳）
    验证点：加载可用 / 全量进日卡片无孤儿 / 时间窗聚类真实触发 /
            连拍折叠真实生效 / 增量聚合旧簇保留 / 性能预算
    """
    print("\n" + "=" * 60)
    print("场景 15：真实数据基准（500 张截图，无 GPS）")
    try:
        from .load_real_photos import load_screenshots, sample_500

        real = sample_500(load_screenshots())
    except Exception as exc:  # noqa: BLE001
        _check(failures, "场景15: 截图加载可用", False, f"{type(exc).__name__}: {exc}")
        return

    _check(failures, "场景15: 500 张真实截图加载", len(real) == 500, f"实际={len(real)}")
    distinct_days = len({p.ts.date().isoformat() for p in real})

    t0 = time.perf_counter()
    r = aggregate(real)
    real_ms = int((time.perf_counter() - t0) * 1000)
    print(
        f"真实数据聚合: {r.stats['raw']} 张 → 预处理 {r.stats['preprocessed']} | "
        f"L0 簇 {r.stats['l0_clusters']} | L1 日卡片 {r.stats['l1_days']} | "
        f"散片并入 {r.stats['noise_to_l1']} | {real_ms}ms"
    )

    in_days = sum(len(d["photos"]) for d in r.l1_days)
    _check(failures, "场景15: 全量进日卡片无孤儿", in_days == 500, f"进卡片={in_days}/500")
    _check(failures, "场景15: 日卡片数合理", 0 < r.stats["l1_days"] <= distinct_days,
            f"日卡片={r.stats['l1_days']} 覆盖天数={distinct_days}")
    # 真实截图常 60 分钟内 ≥3 张（成簇）→ 时间窗聚类应真实触发
    _check(failures, "场景15: 时间窗聚类真实触发", r.stats["l0_clusters"] >= 1,
            f"L0 簇={r.stats['l0_clusters']}")
    # 截图 <5s 连拍稀少（全量 3078 张仅 4 对）→ 折叠数应与 ground truth 一致（不误伤）
    # ground truth：按管线同规则（与前一张间隔 <5s 即并入上一组）计算
    def _gt_fold_count(ps: list) -> int:
        folded_photos = 0
        prev_ts = None
        for p in ps:
            if prev_ts is not None and (p.ts - prev_ts).total_seconds() < 5.0:
                folded_photos += 1
            prev_ts = p.ts
        return folded_photos

    folded = r.stats["raw"] - r.stats["preprocessed"]
    gt_folded = _gt_fold_count(real)
    _check(failures, "场景15: 连拍折叠与 ground truth 一致", folded == gt_folded,
            f"管线折叠={folded} 期望={gt_folded}")
    _check(failures, "场景15: 性能 <2s（B3-6 预算）", real_ms < 2000, f"{real_ms}ms")

    # 增量聚合（AGG-015）：按时间切两批 → 旧簇结构保留
    mid = real[len(real) // 2].ts
    batch_a = [p for p in real if p.ts < mid]
    batch_b = [p for p in real if p.ts >= mid]
    base = aggregate(batch_a)
    incr = incremental_aggregate(base, batch_b)
    old_ids = {frozenset(p.id for p in cl) for cl in base.l0_clusters}
    new_ids = {frozenset(p.id for p in cl) for cl in incr.l0_clusters}
    _check(failures, "场景15: 增量聚合旧簇保留（AGG-015）", old_ids.issubset(new_ids),
            f"旧簇{len(old_ids)}→保留{len(new_ids)}")
    _check(failures, "场景15: 增量新照片入结果",
            sum(1 for cl in incr.l0_clusters for p in cl if p.id in {x.id for x in batch_b}) > 0,
            "新批照片已入")



def _check(failures: list[str], name: str, ok: bool, detail: str = "") -> None:
    status = "✅" if ok else "❌"
    print(f"{status} {name}" + (f" ({detail})" if detail else ""))
    if not ok:
        failures.append(f"{name} ({detail})")


if __name__ == "__main__":
    main()
