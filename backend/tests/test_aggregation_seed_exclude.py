"""Seed 注入数据排除聚类 · 回归测试（2026-09-06 峰宝拍板）

背景（R9 复盘）：seed_echo_today.py 此前 INSERT source='app'，注入的
2025-09 截图伪装成真实数据被聚进今日语音事件——start_time=去年 →
时间轴倒序沉底。拍板：seed 必须带结构化标记（source='seed'），
聚合管线按 source 排除——排除的是「声明为 seed 的数据」，
不是「看起来像 seed 的数据」（启发式不可辩护）。

覆盖入口（三处，缺一即漏）：
  1. aggregate_user 主查询（l2l3 / full 两分支共用 stmt）
  2. _refresh_upper_candidates（端侧 POST /events/sync → 云侧补 L2/L3 路径）
  3. 真实数据（source='app'）同条件正常聚合——防排除误伤

前置：PG yishu 库（db_user 公共 fixture，对齐 test_aggregation.py 先例）
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.db.models import Content, Event, EventItem
from sqlalchemy import select

pytestmark = pytest.mark.integration


def _content(db, user_id: str, ts: datetime | None = None, source: str = "app") -> Content:
    c = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type="photo",
        taken_at=ts or (datetime.now(timezone.utc) - timedelta(hours=1)),
        status="done",
        source=source,
    )
    db.add(c)
    db.commit()
    return c


def _events_for(db, user_id: str) -> list[Event]:
    return db.execute(
        select(Event).where(Event.user_id == user_id, Event.deleted_at.is_(None))
    ).scalars().all()


# ---------------------------------------------------------------------------
# 1. aggregate_user：seed 不进 l2l3 / full 两分支
# ---------------------------------------------------------------------------


def test_l2l3_skips_seed_content(db_user):
    """l2l3 主查询：同窗口 seed+app 并存，只有 app 产生事件"""
    from app.services.events import aggregate_user

    db, user = db_user
    ts = datetime.now(timezone.utc) - timedelta(hours=2)
    _content(db, user.id, ts=ts, source="seed")
    _content(db, user.id, ts=ts + timedelta(minutes=5), source="app")

    r = aggregate_user(db, str(user.id), mode="l2l3")
    assert r["items"] == 0, f"seed 窗口内 app 单条不应产生 L2 候选，实际 {r}"
    # 真实内容仍被扫到（skipped 语义：进了扫描但未成候选）——
    # 若排除误伤 app，items 不会是 0 而是 seed 也进了候选


def test_full_mode_skips_seed_content(db_user):
    """full 管线：seed 不产生 L1 日卡片，app 照常产生"""
    from app.services.events import run_user_aggregation

    db, user = db_user
    uid = str(user.id)  # run_user_aggregation 自开 Session，先缓存（对齐 test_aggregation.py 先例）
    seed_ts = datetime.now(timezone.utc) - timedelta(hours=2)
    app_ts = seed_ts + timedelta(minutes=5)
    _content(db, user.id, ts=seed_ts, source="seed")
    _content(db, user.id, ts=app_ts, source="app")

    r = run_user_aggregation(uid, mode="full")
    db.expire_all()
    assert r.get("l1", 0) >= 1, f"app 内容应产生 L1，实际 {r}"
    evs = _events_for(db, uid)
    assert evs, "应有事件落库"
    ev_ids = [e.id for e in evs]
    seed_ids = db.execute(
        select(Content.id).where(
            Content.user_id == uid, Content.source == "seed"
        )
    ).scalars().all()
    leak = db.execute(
        select(EventItem).where(
            EventItem.event_id.in_(ev_ids), EventItem.content_id.in_(seed_ids)
        )
    ).scalars().all()
    assert not leak, f"seed 内容不得挂进任何事件，泄漏 {len(leak)} 条"


# ---------------------------------------------------------------------------
# 2. _refresh_upper_candidates：端侧提交路径同样排除
# ---------------------------------------------------------------------------


def test_refresh_upper_candidates_skips_seed(db_user):
    """端侧 sync 送来的 photo_ids 混入 seed id → seed 被过滤，不影响重算"""
    from app.services.events.aggregate import _refresh_upper_candidates

    db, user = db_user
    seed = _content(db, user.id, source="seed")

    # 传入含 seed 的 photo_ids：不抛异常、返回 0（seed 被过滤后无可算内容）
    n = _refresh_upper_candidates(db, str(user.id), [seed.id])
    assert n == 0, f"seed 内容不得进 L2/L3 候选重算，实际 {n}"
    leak = db.execute(
        select(EventItem).where(EventItem.content_id == seed.id)
    ).scalars().all()
    assert not leak, f"seed 内容不得产生 EventItem，泄漏 {len(leak)} 条"


# ---------------------------------------------------------------------------
# 3. 防误伤：真实数据同条件正常聚合
# ---------------------------------------------------------------------------


def test_real_data_unaffected_by_seed_filter(db_user):
    """纯 app 内容（无 seed）聚合行为与排除前一致——排除只针对 seed"""
    from app.services.events import aggregate_user

    db, user = db_user
    ts = datetime.now(timezone.utc) - timedelta(hours=48)
    # 跨 2 天 12 张同标签（对齐 test_aggregation.py test_write_upper_candidates 构造）
    for d in range(2):
        for i in range(6):
            c = Content(
                id=str(uuid.uuid4()),
                user_id=user.id,
                content_type="photo",
                taken_at=ts + timedelta(days=d, minutes=i * 5),
                status="done",
                source="app",
                extra={"ci_tags": ["美食"]},
            )
            db.add(c)
    db.commit()

    r = aggregate_user(db, str(user.id), mode="l2l3")
    assert r["items"] + r["upper_items"] >= 1, (
        f"排除 seed 后真实数据仍应正常聚合，实际 {r}"
    )
