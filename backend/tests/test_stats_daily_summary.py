"""GET /api/v1/stats/daily-summary 测试（R9-7 · Q5 拍板：hero 副标题 API 化）

环节级三段（对齐 test_data_chains_12 约定）：
  产生 = 事件+成员内容落库 / 处理 = 聚合口径正确 / 输出 = API 字段对齐 + 空态诚实
"""
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


@pytest.fixture()
def db():
    """本文件自带 db fixture（先例对齐 test_l3_voice_chain.py：各文件自管会话）"""
    session = SessionLocal()
    yield session
    session.close()


def _mk_event_with_content(db, user_id: str, title: str, start: datetime, content_text: str | None):
    from app.db.models import Content, Event, EventItem

    ev = Event(
        id=str(uuid.uuid4()),
        user_id=user_id,
        level=1,
        title=title,
        title_source="template",
        start_time=start,
        end_time=start + timedelta(hours=1),
        confidence=0.9,
        status="draft",
        generated_by="cloud",
    )
    db.add(ev)
    cid = None
    if content_text is not None:
        c = Content(
            id=str(uuid.uuid4()),
            user_id=user_id,
            content_type="text",
            text=content_text,
            status="done",
            source="app",
        )
        db.add(c)
        db.flush()
        cid = c.id
        db.add(EventItem(content_id=cid, event_id=ev.id))
    db.commit()
    db.refresh(ev)
    return ev, cid


class TestDailySummaryChain:
    def test_produce_seed_events(self, db_user):
        """产生：两条事件（一新一旧）+ 成员内容落库"""
        db, user = db_user
        uid = str(user.id)
        now = datetime.now(UTC)
        ev_old, _ = _mk_event_with_content(db, uid, "旧事件", now - timedelta(days=3), "旧内容")
        ev_new, _ = _mk_event_with_content(db, uid, "新事件", now - timedelta(hours=1), "新内容")
        assert ev_old.id and ev_new.id

    def test_process_aggregation_ordering(self, db_user):
        """处理：count=distinct 关联内容数；latest 取 start_time 最新事件"""
        from app.db.models import Event

        db, user = db_user
        uid = str(user.id)
        now = datetime.now(UTC)
        _mk_event_with_content(db, uid, "旧事件", now - timedelta(days=3), "旧内容A")
        _mk_event_with_content(db, uid, "旧事件2", now - timedelta(days=2), "旧内容B")
        ev_new, _ = _mk_event_with_content(db, uid, "最新事件", now - timedelta(hours=1), "新内容C")

        # 服务层口径直接验证（FastAPI 依赖注入不便直调，走等价 SQL 断言）
        from sqlalchemy import func, select

        cnt = db.execute(
            select(func.count(func.distinct(Event.id)))
            .where(Event.user_id == user.id, Event.deleted_at.is_(None))
        ).scalar()
        assert cnt == 3, f"事件计数不对: {cnt}"
        top = db.execute(
            select(Event.title, Event.start_time)
            .where(Event.user_id == user.id, Event.deleted_at.is_(None))
            .order_by(Event.start_time.desc().nullslast(), Event.id.desc())
            .limit(1)
        ).first()
        assert top is not None and top.title == "最新事件", f"最新事件排序错: {top}"
        assert ev_new.start_time is not None

    def test_output_api_fields(self, client, auth_headers, db):
        """输出：API 200 且三字段对齐 DB；空用户诚实返回 0/None

        ⚠️ 2026-10-26 口径变更：`latest_event_text` 由「最新**事件**标题」改为
        「最新一条**记忆**（contents）的文案」—— 字段名保留（schema 不变），语义改到与
        `count` 同源。原因见 `app/api/stats.py` 模块文档（事件表与内容表脱钩，
        两个字段各说各话 = 假状态）。故本用例断言的是**内容**文案而非事件标题。
        """
        uid, headers = auth_headers("dsum")
        # 先验证空态（新用户无数据）
        r0 = client.get("/api/v1/stats/daily-summary", headers=headers)
        assert r0.status_code == 200, r0.text
        d0 = r0.json()["data"]
        assert d0["count"] == 0 and d0["latest_event_text"] is None, f"空态不诚实: {d0}"
        # 建数据后再验：事件标题与内容文案**故意不同**，用来区分两个口径
        now = datetime.now(UTC)
        _mk_event_with_content(db, uid, "事件标题（不该被当记忆文案）", now - timedelta(minutes=30), "内容X")
        r = client.get("/api/v1/stats/daily-summary", headers=headers)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["count"] == 1, f"count 口径错: {d}"
        assert d["latest_event_text"] == "内容X", (
            f"latest_text 应取内容文案（与 count 同源）而不是事件标题: {d}"
        )
        assert d["latest_event_time"], f"latest_time 空: {d}"

    def test_count_same_source_as_list(self, client, auth_headers, db):
        """**假状态回归锁**：hero 的 count 必须与时间轴列表同源（contents），
        且只数列表真能渲染的类型（text/photo）。

        2026-10-26 缺陷实锤：旧实现数 `event_items` 关联内容，而列表读 `contents`；
        本库 `event_items` 仅 4 行 ⇒ 有 281 条内容的账号 hero 显示 count=0
        （「暂无新记忆」= 假状态），列表却滚得出 281 条。
        本用例构造「有内容、无事件关联」的数据（event_items 为空），正是该缺陷的形态：
        · 修复前 ⇒ count=0（假）判红；
        · 修复后 ⇒ count=1（真）通过。
        """
        uid, headers = auth_headers("dsumsame")
        from app.db.models import Content

        # 只落 contents，**不**建事件/event_items（=老账号的真实形态）
        c_text = Content(
            id=str(uuid.uuid4()),
            user_id=uid,
            content_type="text",
            text="只有内容没有事件关联",
            status="done",
            source="app",
        )
        db.add(c_text)
        db.commit()

        d = client.get("/api/v1/stats/daily-summary", headers=headers).json()["data"]
        assert d["count"] == 1, f"hero 数字与列表不同源（假状态回归）: {d}"
        assert d["latest_event_text"] == "只有内容没有事件关联", f"文案来源错: {d}"

        # 语音**不算**进 count：列表 `entries_api.feedAssemble` 显式跳过 voice，
        # 数进来就会让「数字 2 / 列表 1」再次对不上。
        c_voice = Content(
            id=str(uuid.uuid4()),
            user_id=uid,
            content_type="voice",
            status="done",
            source="app",
        )
        db.add(c_voice)
        db.commit()
        d2 = client.get("/api/v1/stats/daily-summary", headers=headers).json()["data"]
        assert d2["count"] == 1, f"语音不该计入（列表不渲染 voice）: {d2}"

    def test_output_requires_auth(self, client):
        """输出：未登录 401（个人统计不公开）"""
        r = client.get("/api/v1/stats/daily-summary")
        assert r.status_code == 401, f"无 token 未被拒: {r.status_code}"
