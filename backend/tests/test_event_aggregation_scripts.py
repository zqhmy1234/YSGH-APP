"""event_aggregation 数据/校验脚本覆盖测试（R8#10/#11 2026-08-27）

侦察：generate_test_photos（70 stmts）/ load_real_photos（89）/ run_validation
（140）三个脚本 0% 覆盖——核心算法 st_dbscan（100%）/pipeline（92%）有测，
但数据生成/加载/校验脚本零覆盖，误导覆盖率报告。

本文件用各脚本的 importable 纯函数补测（**不修改脚本本体**——脚本不在 D2
文件域；"标记 skipif / 移入 scripts 测试组"的归组方案留集成 Agent 统一裁定）：
- generate_test_photos.generate()：B3 十类分布 + 边界用例计数 / 无 GPS / 连拍
- load_real_photos：无目录空列表 / 命名时间戳解析 / 按月分层抽样
- run_validation._check：判定收集语义（纯函数）
- run_validation.validate_real_data：无截图时安全跳过（不误报失败）
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.services.event_aggregation.generate_test_photos import generate
from app.services.event_aggregation.load_real_photos import RawPhoto, load_screenshots, sample_500

# ---------------------------------------------------------------------------
# generate_test_photos.generate()（B3 十类分布 + 边界用例）
# ---------------------------------------------------------------------------


def test_generate_returns_500_scale_with_all_scenarios():
    """批量扩充至 ~500 张，且覆盖全部场景前缀（p1~p13）"""
    photos = generate()
    assert len(photos) >= 490
    ids = [p.id for p in photos]
    for prefix in (
        "p1-", "p2a-", "p2b-", "p3-", "p4-", "p5a-", "p5b-",
        "p6-", "p7-", "p8-", "p9-", "p10-", "p11-", "p12-", "p13-",
    ):
        assert any(x.startswith(prefix) for x in ids), f"缺少场景前缀 {prefix}"


def test_generate_scenario6_no_gps():
    """场景 6：无 GPS 照片（截图/微信图）——lat/lng 为 None，走按时间归组路径"""
    p6 = [p for p in generate() if p.id.startswith("p6-")]
    assert len(p6) == 5
    assert all(p.lat is None and p.lng is None for p in p6)
    assert all("截图" in (p.tags or []) for p in p6)


def test_generate_scenario7_burst_and_scenario9_dedup():
    """场景 7：20 连拍（间隔 3s <5s 折叠阈值）；场景 9：重复哈希标记"""
    photos = generate()
    p7 = [p for p in photos if p.id.startswith("p7-")]
    assert len(p7) == 20
    p9 = [p for p in photos if p.id.startswith("p9-")]
    assert len(p9) == 3
    assert all(p.ocr_text == "DUP-HASH-001" for p in p9)


# ---------------------------------------------------------------------------
# load_real_photos（真实截图加载 / 分层抽样）
# ---------------------------------------------------------------------------


def test_load_screenshots_empty_without_dir():
    """未设置 SCREENSHOT_DIR / 目录不存在 → 空列表（场景15 安全跳过）"""
    from pathlib import Path

    assert load_screenshots(None) == []
    assert load_screenshots(directory=Path("C:/nonexistent-dir-xyz")) == []


def test_load_screenshots_parses_named_timestamps(tmp_path):
    """命名时间戳解析 + 非匹配文件忽略 + 时间升序"""
    (tmp_path / "屏幕截图 2026-07-01 120000.png").write_bytes(b"x")
    (tmp_path / "屏幕截图 2026-07-01 123000.png").write_bytes(b"x")
    (tmp_path / "not-a-screenshot.txt").write_text("x")
    photos = load_screenshots(directory=tmp_path)
    assert len(photos) == 2
    assert photos[0].ts < photos[1].ts
    assert all(p.source == "screenshot" for p in photos)


def test_sample_500_stratified_respects_daily_quota(tmp_path):
    """按月分层抽样：单日配额生效（max_per_day=1 → 每天至多 1 张）"""
    base = datetime(2026, 7, 1, 9, 0)  # noqa: DTZ001 —— 与 load_real_photos 朴素时间契约一致（无时区字段）
    photos = [
        RawPhoto(id=f"m-{day}-{i}", ts=base + timedelta(days=day, hours=i))
        for day in range(30)
        for i in range(2)
    ]
    picked = sample_500(photos, target=30, max_per_day=1)
    assert len(picked) == 30
    assert len({p.ts.date().isoformat() for p in picked}) == 30  # 30 天 × 1 张


# ---------------------------------------------------------------------------
# run_validation（判定逻辑）
# ---------------------------------------------------------------------------


def test_run_validation_check_pure():
    """_check 纯函数：通过不收集 / 失败收集（name + detail）"""
    from app.services.event_aggregation.run_validation import _check

    failures: list[str] = []
    _check(failures, "场景1: 通过", True, "ok")
    assert failures == []
    _check(failures, "场景2: 失败", False, "got=1")
    assert failures == ["场景2: 失败 (got=1)"]


def test_validate_real_data_skips_without_screenshots(monkeypatch):
    """无真实截图 → 场景15 安全跳过（不误报失败、不抛异常）"""
    import app.services.event_aggregation.load_real_photos as lrp
    import app.services.event_aggregation.run_validation as rv

    monkeypatch.setattr(lrp, "load_screenshots", lambda *a, **k: [])
    failures: list[str] = []
    rv.validate_real_data(failures)
    assert failures == []
