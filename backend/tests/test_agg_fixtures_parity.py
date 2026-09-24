"""AGG-016 云侧夹具回归（D04-2 / D04-15 修复件 · 功能修复波 2026-09-25）。

## 为什么新增这个文件

审计结论（D04-2/D04-15）：「AGG-016 双跑门禁**无鉴别力**，端云"同源契约"实际已分叉」。
根因有三层：

1. `scripts/gen_agg_fixtures.py` 原本**只产出端侧 UTS 夹具**（`client/utils/agg/fixtures.uts`），
   **云侧没有任何夹具消费方** —— `test_agg_reference.py` 只是几处手写语义锁；
2. 于是「12 项共享数值零漂移」的结论**建立在端侧单跑之上**；云侧缺去重（D04-2）、
   日界走 UTC（D04-1）这类分叉**不会被任何门禁发现**；
3. `run_validation.py` 的 AGG-016 断言自比常量-常量（恒真，D04-14），进一步制造"已校验"错觉。

本文件让**云侧真实实现**跑**同一份夹具**并与期望逐条比对 ⇒ AGG-016 从"端侧单跑 + 恒真断言"
变成真正的双跑门禁。比对语义与端侧 `client/utils/agg/agg_check.uts` **逐字对齐**：
簇＝集合语义（簇内 id 排序 → 簇间排序 → `;` 连接）、日卡片＝`date|排序 id|稀疏标记`。

## ⚠️ 为什么必须跑**冻结快照**（而不是现场 `build_cases()`）

第一版实现用 `build_cases()` 现场取输入与期望 —— 但生成器的期望是**调用云侧实现**算出来的
（`_expected()` → `preprocess`/`st_dbscan`/`l1_daily_aggregate`）⇒ 关掉任一分支时"输入侧"与
"期望侧"**一起变**，门禁**自证**、抓不到回归（实测：corrected/approx/degraded 三个分支探针全绿）。
故生成器额外落盘 [`backend/tests/data/agg_fixtures.json`](file:///d:/GuangH-App/backend/tests/data/agg_fixtures.json)
（**输入 + 期望**都冻结），本门禁跑**冻结输入**、比**冻结期望** ⇒ 分支改坏立刻不一致。
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

import pytest
from app.services.event_aggregation.agg_types import (
    L0_EPS_S_M,
    L0_EPS_T_SEC,
    L0_EPS_T_SEC_CONSERVATIVE,
    L0_MIN_PTS,
    RawPhoto,
)
from app.services.event_aggregation.pipeline import preprocess
from app.services.event_aggregation.st_dbscan import l1_daily_aggregate, st_dbscan

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = REPO_ROOT / "backend" / "tests" / "data" / "agg_fixtures.json"

with SNAPSHOT_PATH.open(encoding="utf-8") as fh:
    SNAPSHOT = json.load(fh)

CASES = SNAPSHOT["cases"]


def _norm_clusters(clusters: list[list[str]]) -> str:
    parts = [",".join(sorted(c)) for c in clusters]
    return ";".join(sorted(parts))


def _day_keys(days: list[dict]) -> str:
    keys = [
        f"{d['date']}|{','.join(sorted(d['ids']))}|{'1' if d['is_sparse'] else '0'}"
        for d in days
    ]
    return ";".join(sorted(keys))


def _run_cloud(case: dict) -> tuple[list[list], list[dict]]:
    """用**云侧真实实现**跑一个**冻结**夹具用例（参数口径与端侧 agg_check.uts 一致）。"""
    raws = [
        RawPhoto(
            id=p["id"],
            ts=datetime.fromtimestamp(p["ts_ms"] / 1000, tz=timezone.utc),
            lat=p["lat"],
            lng=p["lng"],
            tags=list(p["tags"]),
            phash=p["phash"],
        )
        for p in case["photos"]
    ]
    eps_t = L0_EPS_T_SEC_CONSERVATIVE if case.get("conservative") else L0_EPS_T_SEC
    pts = preprocess(raws)
    clusters = st_dbscan(pts, eps_t_sec=eps_t, eps_s_m=L0_EPS_S_M, min_pts=L0_MIN_PTS)
    clustered = {p.id for cl in clusters for p in cl}
    noise = [p for p in pts if p.id not in clustered]
    days = l1_daily_aggregate(clusters, noise, tz_offset_minutes=case["tz"])
    return clusters, days


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_cloud_matches_frozen_fixture(case: dict) -> None:
    clusters, days = _run_cloud(case)

    actual_clusters = [[p.id for p in cl] for cl in clusters]
    assert _norm_clusters(actual_clusters) == _norm_clusters(case["expected"]["clusters"]), (
        f"[{case['name']}] 簇与**冻结**期望不一致（端云分叉 / 分支回归）"
    )

    actual_days = [
        {"date": d["date"], "ids": [p.id for p in d["photos"]], "is_sparse": d["is_sparse"]}
        for d in days
    ]
    assert _day_keys(actual_days) == _day_keys(case["expected"]["days"]), (
        f"[{case['name']}] 日卡片与**冻结**期望不一致（端云分叉 / 分支回归）"
    )


def test_frozen_snapshot_is_up_to_date() -> None:
    """快照新鲜度：`build_cases()` 现在必须**逐字等于**冻结快照。

    两个作用：① 有人改了用例/实现却忘了重生成 ⇒ 立刻报（golden file 纪律）；
    ② 反过来也是**分支回归**的探测器（生成器期望由实现算出，实现被改坏 ⇒ 与冻结值不符）。
    """
    from scripts.gen_agg_fixtures import build_cases, render_json

    assert render_json(build_cases()) == SNAPSHOT, (
        "夹具快照已过期：请重跑 `python scripts/gen_agg_fixtures.py`（并提交两份产物）"
    )


def test_drift_branches_are_covered() -> None:
    """D04-15 反向保护：夹具里必须**存在**覆盖 frozen/approx/corrected/degraded 三态的用例。

    这三态此前完全落在夹具覆盖之外（审计 D04-15）；本断言把"覆盖"这件事钉住 ——
    有人删掉/改名这些用例时门禁会响（避免又退化成"端云双跑全绿但三态没人测"）。
    """
    names = [c["name"] for c in CASES]
    for must in ("drift-corrected-single-point", "drift-approx-moving", "drift-degraded-no-mode"):
        assert must in names, f"夹具缺失漂移分支用例 {must}：{names}"

    # 去重用例亦须有**真实重复**哈希（否则该用例不构成去重鉴别）
    dedup = [c for c in CASES if "dedup" in c["name"]]
    assert dedup, "夹具缺失去重用例"
    assert any(
        len([p["phash"] for p in c["photos"] if p["phash"]])
        != len({p["phash"] for p in c["photos"] if p["phash"]})
        for c in dedup
    ), "去重用例里没有真实重复的 phash ⇒ 不构成去重鉴别"
