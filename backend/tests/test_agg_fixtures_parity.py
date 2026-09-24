"""AGG-016 云侧夹具回归（D04-2 / D04-15 修复件 · 功能修复波 2026-09-25）。

## 为什么新增这个文件

审计结论（D04-2/D04-15）：「AGG-016 双跑门禁**无鉴别力**，端云"同源契约"实际已分叉」。
根因有三层：

1. `scripts/gen_agg_fixtures.py` **只产出端侧 UTS 夹具**（`client/utils/agg/fixtures.uts`），
   **云侧没有任何夹具消费方** —— `test_agg_reference.py` 只是几处手写语义锁；
2. 于是「12 项共享数值零漂移」的结论**建立在端侧单跑之上**；云侧缺去重（D04-2）、
   日界走 UTC（D04-1）这类分叉**不会被任何门禁发现**；
3. `run_validation.py` 的 AGG-016 断言自比常量-常量（恒真，D04-14），进一步制造"已校验"错觉。

本文件让**云侧真实实现**跑**同一份夹具**并与期望逐条比对 ⇒ AGG-016 从"端侧单跑 + 恒真断言"
变成真正的双跑门禁。比对语义与端侧 `client/utils/agg/agg_check.uts` **逐字对齐**：
簇＝集合语义（簇内 id 排序 → 簇间排序 → `;` 连接）、日卡片＝`date|排序 id|稀疏标记`。

夹具输入里的 `phash` 在生成器里是**旁挂字典**（`case["phash"]`，因为旧 `RawPhoto` 没有该字段）；
D04-2 修好后云侧契约已补 `phash`，此处把它贴回 `RawPhoto` 后再跑 —— 这正是"重复照片"用例的输入。
"""
from __future__ import annotations

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

from scripts.gen_agg_fixtures import build_cases

CASES = build_cases()


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
    """用**云侧真实实现**跑一个夹具用例（参数口径与端侧 agg_check.uts 一致）。"""
    phash_by_id = case.get("phash") or {}
    raws = [
        RawPhoto(
            id=p.id,
            ts=p.ts,
            lat=p.lat,
            lng=p.lng,
            tags=list(p.tags or []),
            phash=phash_by_id.get(p.id) or "",
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
def test_cloud_matches_uts_fixture(case: dict) -> None:
    clusters, days = _run_cloud(case)

    actual_clusters = [[p.id for p in cl] for cl in clusters]
    assert _norm_clusters(actual_clusters) == _norm_clusters(case["expected"]["clusters"]), (
        f"[{case['name']}] 簇与夹具期望不一致（端云分叉）"
    )

    actual_days = [
        {"date": d["date"], "ids": [p.id for p in d["photos"]], "is_sparse": d["is_sparse"]}
        for d in days
    ]
    assert _day_keys(actual_days) == _day_keys(case["expected"]["days"]), (
        f"[{case['name']}] 日卡片与夹具期望不一致（端云分叉）"
    )


def test_dedup_case_is_present_and_discriminating() -> None:
    """D04-2 反向保护：夹具里必须**存在**"同哈希重复照片"用例，否则本门禁又会退化为空转。

    审计指出该场景此前无法被云侧表达（`RawPhoto` 无 `phash`）；本断言把「有重复哈希用例」
    这件事本身钉住 —— 有人删掉/改名该用例时门禁会响。
    """
    names = [c["name"] for c in CASES]
    assert any("dedup" in n for n in names), f"夹具缺失去重用例：{names}"

    dedup_cases = [c for c in CASES if "dedup" in c["name"]]
    exercised = False
    for c in dedup_cases:
        hashes = [p for p in (c.get("phash") or {}).values() if p]
        if len(hashes) != len(set(hashes)):
            exercised = True
    assert exercised, "去重用例里没有**真实重复**的 phash ⇒ 该用例不构成去重鉴别"
