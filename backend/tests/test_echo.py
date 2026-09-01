"""回响测试（P2-ECHO · 去年今日）

覆盖：
  - 去年今日内容命中 + 返回指纹
  - 每天 ≤1 条（同日第二次 → None）
  - 竞态兜底：uq_echo_history_daily 部分唯一索引（并发双插入只有一条成功）
  - 本地日界（修复：原 UTC 日界，本地 0:00-8:00 算前一天）
  - dismiss 划掉后不再出现
  - 敏感内容排除（sensitive_status ≠ 正常）
  - API 冒烟
前置：PG yishu 库
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.db.models import Content, EchoHistory, ProfileSensitive
from app.services import echo as echo_svc
from app.services.echo import dismiss_echo, get_today_echo
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

pytestmark = pytest.mark.integration


def _last_year_content(db, user_id: str, text: str = "去年的今天", sensitive: str = "正常") -> Content:
    # 本地日界（与 get_today_echo 口径一致：本地今天 → 去年同月同日；闰年退化 2/28）
    today = echo_svc._local_now().date()
    try:
        last_year = today.replace(year=today.year - 1)
    except ValueError:
        last_year = today.replace(year=today.year - 1, day=28)
    ts = datetime.combine(last_year, datetime.min.time(), tzinfo=echo_svc._local_now().tzinfo)
    c = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type="text",
        text=text,
        taken_at=ts,
        sensitive_status=sensitive,
        status="done",
    )
    db.add(c)
    db.commit()
    return c


def test_echo_hits_last_year(db_user):
    db, user = db_user
    c = _last_year_content(db, user.id)
    result = get_today_echo(db, user.id)
    assert result is not None
    assert result["content_id"] == c.id
    assert result["fingerprint"]


def test_echo_daily_limit(db_user):
    """每天 ≤1 条：第一条命中后，同日第二次 → None"""
    db, user = db_user
    _last_year_content(db, user.id, "第一条")
    r1 = get_today_echo(db, user.id)
    assert r1 is not None
    r2 = get_today_echo(db, user.id)
    assert r2 is None, "同日第二次不应再返回回响"


def test_echo_daily_unique_index_blocks_race(db_user):
    """竞态兜底（审查 MAJOR）：部分唯一索引 (user_id, shown_date) WHERE action
    <> 'dismiss'——绕过查询计数直接并发插两条 respond，第二条必须 IntegrityError"""
    db, user = db_user
    today = echo_svc._local_now().date()
    db.add(EchoHistory(user_id=user.id, event_id=None, action="respond", shown_date=today))
    db.commit()
    db.add(EchoHistory(user_id=user.id, event_id=None, action="respond", shown_date=today))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    # dismiss 不参与每天≤1 条：同一天可多条 dismiss
    db.add(EchoHistory(user_id=user.id, event_id=None, action="dismiss", shown_date=today))
    db.add(EchoHistory(user_id=user.id, event_id=None, action="dismiss", shown_date=today))
    db.commit()


def test_echo_local_day_boundary(db_user, monkeypatch):
    """本地日界（审查 MINOR）：本地 0:00-8:00 归属本地当天，不算前一天

    固定本地时间 2026-01-02 00:30 (+08:00)——UTC 仍是 2026-01-01。
    去年今日按本地日期取 2025-01-02；旧实现按 UTC 取 2025-01-01 会漏命中。
    """
    local_tz = timezone(timedelta(hours=8))
    monkeypatch.setattr(
        echo_svc, "_local_now", lambda: datetime(2026, 1, 2, 0, 30, tzinfo=local_tz)
    )
    db, user = db_user
    # 去年今日 = 2025-01-02（本地日界）
    c = Content(
        id=str(uuid.uuid4()),
        user_id=user.id,
        content_type="text",
        text="本地日界测试",
        taken_at=datetime(2025, 1, 2, 10, 0, tzinfo=local_tz),
        sensitive_status="正常",
        status="done",
    )
    db.add(c)
    db.commit()
    result = get_today_echo(db, user.id)
    assert result is not None, "本地日界下应命中 2025-01-02 的内容"
    assert result["content_id"] == c.id
    assert result["echo_date"] == "2026-01-02"
    assert result["taken_at"].startswith("2025-01-02")


def test_echo_local_day_limit_boundary(db_user, monkeypatch):
    """本地日界下"每天≤1 条"：本地当天 00:10 已展示 → 当天不再返回"""
    local_tz = timezone(timedelta(hours=8))
    monkeypatch.setattr(
        echo_svc, "_local_now", lambda: datetime(2026, 1, 2, 0, 30, tzinfo=local_tz)
    )
    db, user = db_user
    _last_year_content(db, user.id, "日界限制")
    # 本地 2026-01-02 00:10（UTC 2026-01-01 16:10）已展示 → 旧 UTC 逻辑把它算昨天
    db.add(
        EchoHistory(
            user_id=user.id,
            event_id=None,
            action="respond",
            shown_at=datetime(2026, 1, 1, 16, 10, tzinfo=timezone.utc),
            shown_date="2026-01-02",
        )
    )
    db.commit()
    assert get_today_echo(db, user.id) is None, "本地当天已展示过 → 不再返回"


def test_echo_dismiss(db_user):
    """划掉后不再出现"""
    db, user = db_user
    c = _last_year_content(db, user.id)
    assert get_today_echo(db, user.id) is not None
    dismiss_echo(db, user.id, c.id)
    assert get_today_echo(db, user.id) is None


def test_echo_skips_sensitive(db_user):
    """敏感内容排除（sensitive_status ≠ 正常）"""
    db, user = db_user
    _last_year_content(db, user.id, "敏感记忆", sensitive="敏感")
    result = get_today_echo(db, user.id)
    assert result is None


def test_echo_llm_check_blocks_unmarked_sensitive(db_user):
    """审查修复(P1-06，用户拍板：已有敏感标记 + LLM 检测)：
    入库时未标记敏感（sensitive_status=正常）但文本命中规则层敏感词 → 出包前拦截"""
    db, user = db_user
    _last_year_content(db, user.id, "支持法轮功的言论", sensitive="正常")
    result = get_today_echo(db, user.id)
    assert result is None


def _profile_row(db, user_id: str, topic: str, disposition: str) -> ProfileSensitive:
    row = ProfileSensitive(user_id=user_id, topic=topic, disposition=disposition)
    db.add(row)
    db.commit()
    return row


def test_echo_skips_profile_forbid(db_user):
    """画像 L1 校验（B5b FIX-4）：profile_sensitive forbid 命中 → 跳过不重提"""
    db, user = db_user
    _profile_row(db, user.id, "前女友", "forbid")
    _last_year_content(db, user.id, "那天在前女友楼下等她")
    assert get_today_echo(db, user.id) is None


@pytest.mark.parametrize("disposition", ["caution", "review"])
def test_echo_skips_profile_caution_review(db_user, disposition):
    """画像 L1 校验：caution/review 同样跳过不重提（产品口径）"""
    db, user = db_user
    _profile_row(db, user.id, "爸爸", disposition)
    _last_year_content(db, user.id, "爸爸带我去看海")
    assert get_today_echo(db, user.id) is None


def test_echo_profile_allow_passes(db_user):
    """画像 L1 校验：allow/mention 放行（不阻断回响）"""
    db, user = db_user
    _profile_row(db, user.id, "前女友", "allow")
    c = _last_year_content(db, user.id, "那天在前女友楼下等她")
    result = get_today_echo(db, user.id)
    assert result is not None and result["content_id"] == c.id


def test_profile_sensitive_crud(db_user):
    """画像敏感对话式增删查（B1-6）：upsert → 更新 → 查 → 删 → 幂等"""
    from app.services.echo import (
        delete_profile_sensitive,
        list_profile_sensitive,
        upsert_profile_sensitive,
    )

    db, user = db_user
    row = upsert_profile_sensitive(db, user.id, "前女友", "forbid", ["用户原话"], locked=True)
    assert row.disposition == "forbid" and row.locked is True
    row2 = upsert_profile_sensitive(db, user.id, "前女友", "caution")
    assert row2.id == row.id and row2.disposition == "caution"  # 同话题更新
    rows = list_profile_sensitive(db, user.id)
    assert len(rows) == 1 and rows[0].topic == "前女友"
    assert [r.topic for r in list_profile_sensitive(db, user.id, "forbid")] == []
    assert [r.topic for r in list_profile_sensitive(db, user.id, "caution")] == ["前女友"]
    assert delete_profile_sensitive(db, user.id, "前女友") is True
    assert delete_profile_sensitive(db, user.id, "前女友") is False
    assert list_profile_sensitive(db, user.id) == []


def test_profile_sensitive_upsert_race_no_500(db_user):
    """R2#13 竞态修复：并发 upsert 同 (user_id, topic) 不 500（ON CONFLICT 原子 upsert）"""
    import threading

    from app.db.session import SessionLocal
    from app.services.echo import upsert_profile_sensitive

    db, user = db_user
    results: dict = {}

    def worker(n: int):
        s = SessionLocal()
        try:
            row = upsert_profile_sensitive(
                s, user.id, "并发话题", "forbid" if n == 0 else "caution"
            )
            results[n] = row.disposition
        except Exception as exc:  # noqa: BLE001 —— 记录逃逸异常（不应发生）
            results[n] = exc
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(not isinstance(v, Exception) for v in results.values()), results
    rows = db.execute(
        select(ProfileSensitive).where(ProfileSensitive.user_id == user.id)
    ).scalars().all()
    assert len(rows) == 1, "并发 upsert 应只剩一行（原子 upsert 合并）"


def test_profile_sensitive_api_smoke(db_user):
    """API 冒烟（B1-6）：POST/DELETE/GET /api/v1/profile/sensitive（隔离 app 挂载 router）"""
    from app.api import deps
    from app.api.contents import profile_sensitive_router
    from app.core.errors import install_error_handlers
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    db, user = db_user
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(profile_sensitive_router)
    app.dependency_overrides[deps.get_current_user] = lambda: user
    client = TestClient(app)
    try:
        r = client.post(
            "/api/v1/profile/sensitive",
            json={"topic": "前女友", "disposition": "forbid", "locked": True},
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["topic"] == "前女友"
        r = client.get("/api/v1/profile/sensitive")
        assert r.status_code == 200 and len(r.json()["data"]) == 1
        r = client.get("/api/v1/profile/sensitive", params={"disposition": "allow"})
        assert r.json()["data"] == []
        r = client.delete("/api/v1/profile/sensitive", params={"topic": "前女友"})
        assert r.status_code == 200 and r.json()["data"]["deleted"] is True
        r = client.delete("/api/v1/profile/sensitive", params={"topic": "前女友"})
        assert r.status_code == 404
        r = client.post(
            "/api/v1/profile/sensitive",
            json={"topic": "bad", "disposition": "ban"},
        )
        assert r.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_echo_api_smoke(db_user):
    """API 冒烟：GET /today"""
    from app.api import deps
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    _last_year_content(db, user.id, "API 回响")
    client = TestClient(app)

    def fake_user():
        return user

    app.dependency_overrides[deps.get_current_user] = fake_user
    try:
        r = client.get("/api/v1/echo/today")
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert data is not None and data["content_id"]
    finally:
        app.dependency_overrides.clear()
