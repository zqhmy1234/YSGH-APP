"""清尾后的事件回收 —— `content_purge.gc_orphan_events`（2026-10-16）。

## 为什么需要这个用例

真库实证：`event_items.content_id` 是**级联外键**，而清尾路径（`trash_clear` 与 30 天
`run_cleanup`）**只删 contents、不维护父事件** ⇒ 用户清一次回收站/等 30 天，就永久留下
**"有事件、零成员"的空壳**（实测当时：**485 个 L1 事件 + 全表 0 个成员** ✗✗）。
而时间轴读端全靠 `event_items` 取照片/语音/计数（`api/events.py:167/233`）⇒ 卡片全空、
冷启动后什么都看不到（真机反馈"冷启动没内容"的成因之一 ✓）。

本用例直接钉住助手的两条分支（不依赖 HTTP 层，避免把回收行为与回收站语义耦合）：
  ① **成员被删光** ⇒ 事件**软删**（保留审计）+ 写 `event_edit_log(action="gc_empty")`
  ② **仍有成员** ⇒ 只**重算时间窗**（复用事件域 `_refresh_event_window`）
"""
from app.db.models import Content, Event, EventEditLog, EventItem
from app.services.content_purge import gc_orphan_events


def _content(db, user, *, taken_at, text: str) -> Content:
    row = Content(
        user_id=user.id,
        content_type="text",
        source="app",
        text=text,
        status="done",
        taken_at=taken_at,
    )
    db.add(row)
    db.commit()
    return row


def _event_with(db, user, cids: list[str], start) -> Event:
    ev = Event(
        user_id=user.id,
        level=1,
        status="draft",
        title="早上在菜市场",
        start_time=start,
        end_time=start,
    )
    db.add(ev)
    db.flush()
    for cid in cids:
        db.add(EventItem(content_id=str(cid), event_id=str(ev.id)))
    db.commit()
    return ev


def test_remaining_member_recomputes_window(db_user):
    """仍有成员 ⇒ 只重算时间窗（不软删）——成员少了，窗口应收敛到剩余成员范围"""
    db, user = db_user
    from datetime import UTC, datetime, timedelta

    base = datetime(2026, 10, 16, 8, 10, tzinfo=UTC)
    keep = _content(db, user, taken_at=base, text="买菜：西红柿")
    drop = _content(db, user, taken_at=base + timedelta(minutes=2), text="顺手买了鸡蛋")
    ev = _event_with(db, user, [str(keep.id), str(drop.id)], base)

    # 模拟级联：drop 的成员行随内容硬删而消失
    db.query(EventItem).filter(EventItem.content_id == str(drop.id)).delete()
    db.commit()

    soft, refreshed = gc_orphan_events(db, str(user.id), [str(ev.id)])

    db.refresh(ev)
    assert (soft, refreshed) == (0, 1), (soft, refreshed)
    assert ev.deleted_at is None, "还有成员就不该软删"
    assert ev.start_time is not None and ev.start_time <= base + timedelta(seconds=1)


def test_no_member_left_soft_deletes_event(db_user):
    """成员全被清尾 ⇒ 事件软删 + 审计留痕（不物理删）"""
    db, user = db_user
    from datetime import UTC, datetime

    base = datetime(2026, 10, 16, 8, 10, tzinfo=UTC)
    only = _content(db, user, taken_at=base, text="买菜")
    ev = _event_with(db, user, [str(only.id)], base)

    db.query(EventItem).filter(EventItem.event_id == str(ev.id)).delete()
    db.commit()

    soft, refreshed = gc_orphan_events(db, str(user.id), [str(ev.id)])

    db.refresh(ev)
    assert (soft, refreshed) == (1, 0), (soft, refreshed)
    assert ev.deleted_at is not None, "零成员的空壳必须被回收（否则时间轴留一张空卡）"
    logs = db.query(EventEditLog).filter(EventEditLog.event_id == str(ev.id)).all()
    assert any(x.action == "gc_empty" for x in logs), "回收要留审计（可回溯）"


def test_foreign_or_deleted_event_is_skipped(db_user, make_user):
    """非本人 / 已删事件 ⇒ 跳过（不抛错、不误删）——清尾路径必须幂等可重入"""
    db, user = db_user
    other = make_user(db)  # make_user 需要显式传 db（本仓 fixture 口径）
    db_other_ev = Event(user_id=other.id, level=1, status="draft", title="别人的")
    db.add(db_other_ev)
    db.commit()

    soft, refreshed = gc_orphan_events(db, str(user.id), [str(db_other_ev.id)])
    assert (soft, refreshed) == (0, 0)
    db.refresh(db_other_ev)
    assert db_other_ev.deleted_at is None, "不得越权软删他人事件"
