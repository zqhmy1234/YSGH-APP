"""B4 回响 · 交付级集成测试数据矩阵（发现隐藏缺陷，不做走过场）。

与 ``tests/test_echo.py`` 的差别：本文件**不测纯函数**，每个用例都真实构造
Content / EchoPreference / EchoHistory / ProfileSensitive / EchoExclusion 行，
走 ``get_today_echo`` 完整写路径（含消息建发），用 conftest 的 ``db_user`` 接入
本地 PG（127.0.0.1:55432 / yishu）。

覆盖目标：
  闸1 授权 / 闸2 静默 / 闸3 每日上限 / 闸4 角色可见（本期占位，不实现）
  闸5 单条有效性 / 闸6 冷却 / 闸7 精确去年今日 / 闸8 敏感与排除

刁钻场景：闰日 2/29、跨年、DST 与非整点时区偏移、00:00 本地日界、
静默 21:59/22:00/07:59/08:00、max_per_day=0/1/2、dismiss 与冷却交织、
deleted_at 软删、空文本有图、有文本 taken_at=None、profile 五级处置、
LLM 敏感词、多候选首候选被拦截、部分唯一索引并发冲突、用户隔离。

初始运行暴露的两处缺陷（已于 2026-11-24 修复，本文件现作为回归闸门）：
  A. 多候选首候选被 LLM 拒发后回退到未检候选（已修：拒发即整批 None）；
  B. echo_exclusion 用户排除配置从未被服务读取（已修：_load_exclusions 接入八闸）。

四路审判后的追加回归见文件末尾：mask 打码、peek 安全一致、content_id 归属、
extra=forbid、统一错误信封、并发 dismiss 幂等。
"""
from __future__ import annotations

import threading
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
from app.db.models import (
    Content,
    EchoExclusion,
    EchoHistory,
    EchoPreference,
    ProfileSensitive,
)
from app.services import echo as echo_svc
from app.services.echo import get_today_echo, peek_today_echo
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("profile_crypto_keys")]

LOCAL_TZ = timezone(timedelta(hours=8))


# --------------------------------------------------------------------------- 构造器
def _freeze(monkeypatch, now: datetime) -> datetime:
    """冻结八闸唯一时间源；echo 内部所有 _local_now() 调用都命中同一时刻。"""
    monkeypatch.setattr(echo_svc, "_local_now", lambda: now)
    return now


def _put_pref(db, user_id: str, **fields) -> EchoPreference:
    """真实落一行 echo_preference（不存在则建行），避免只靠建表默认值。"""
    row = db.get(EchoPreference, user_id)
    if row is None:
        row = EchoPreference(user_id=user_id)
        db.add(row)
    for key, value in fields.items():
        setattr(row, key, value)
    db.commit()
    return row


def _last_year(today: date) -> date:
    """与 echo_svc._last_year_date 同口径：闰日退化 2/28。"""
    try:
        return today.replace(year=today.year - 1)
    except ValueError:
        return today.replace(year=today.year - 1, day=28)


def _ly_ts(now: datetime, hour: int = 10, minute: int = 0) -> datetime:
    return datetime.combine(_last_year(now.date()), time(hour, minute), tzinfo=now.tzinfo)


def _content(db, user_id: str, taken_at: datetime | None, **kwargs) -> Content:
    """构造一条真实 contents 行；未传的字段走与生产一致的默认值。"""
    payload = dict(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type=kwargs.pop("content_type", "text"),
        text=kwargs.pop("text", "去年今日的普通回忆"),
        taken_at=taken_at,
        sensitive_status=kwargs.pop("sensitive_status", "正常"),
        status="done",
    )
    payload.update(kwargs)
    row = Content(**payload)
    db.add(row)
    db.commit()
    return row


def _history(
    db,
    user_id: str,
    *,
    action: str,
    shown_date: date,
    fingerprint: str | None = None,
    shown_at: datetime | None = None,
    tz: timezone = LOCAL_TZ,
) -> EchoHistory:
    """构造回响历史；shown_at 显式给出，避免 server_default=now() 与冻结时间错配。"""
    row = EchoHistory(
        user_id=user_id,
        action=action,
        shown_date=shown_date,
        fingerprint=fingerprint,
        shown_at=shown_at or datetime.combine(shown_date, time(12, 0), tzinfo=tz),
    )
    db.add(row)
    db.commit()
    return row


def _profile(db, user_id: str, topic: str, disposition: str) -> ProfileSensitive:
    """通过生产 upsert 落画像敏感行（密文 topic + blind index）。"""
    from app.services.echo import upsert_profile_sensitive

    return upsert_profile_sensitive(db, user_id, topic, disposition)


def _assert_echo(got, expected: bool, msg: str = "") -> None:
    assert (got is not None) is expected, (
        f"{msg}：期望{'返回回响' if expected else '返回 None'}，实际 {got!r}"
    )


# =========================================================== 闸1 / 闸2：授权 + 静默
@pytest.mark.parametrize(
    "enabled,hour,minute,start,end,expect",
    [
        pytest.param(True, 12, 0, "22:00", "08:00", True, id="白天默认放行"),
        pytest.param(False, 12, 0, "22:00", "08:00", False, id="闸1关闭授权直接拦截"),
        pytest.param(True, 21, 59, "22:00", "08:00", True, id="静默起点前一分钟21:59放行"),
        pytest.param(True, 22, 0, "22:00", "08:00", False, id="静默起点22:00命中拦截"),
        pytest.param(True, 23, 30, "22:00", "08:00", False, id="深夜23:30命中拦截"),
        pytest.param(True, 7, 59, "22:00", "08:00", False, id="静默终点前一分钟07:59仍拦截"),
        pytest.param(True, 8, 0, "22:00", "08:00", True, id="静默终点08:00放行"),
        pytest.param(True, 0, 0, "22:00", "08:00", False, id="00:00本地日界落在静默窗口"),
        pytest.param(True, 12, 0, "00:00", "00:00", True, id="start等于end表示不设静默"),
        pytest.param(True, 12, 0, "08:00", "22:00", False, id="非跨午夜静默白天命中"),
        pytest.param(True, 23, 0, "08:00", "22:00", True, id="非跨午夜静默夜间放行"),
        pytest.param(False, 23, 0, "00:00", "00:00", False, id="关闭授权优先于关闭静默"),
    ],
)
def test_gate1_2_pref_and_quiet_boundaries(
    db_user, monkeypatch, enabled, hour, minute, start, end, expect
):
    """闸1/闸2 边界矩阵：真实 EchoPreference + Content，验证静默窗口 start 含、end 不含，
    跨午夜与非跨午夜两种口径，以及 21:59/22:00/07:59/08:00 四个临界分钟。"""
    now = _freeze(monkeypatch, datetime(2026, 6, 15, hour, minute, tzinfo=LOCAL_TZ))
    db, user = db_user
    _put_pref(db, user.id, enabled=enabled, quiet_start=start, quiet_end=end)
    c = _content(db, user.id, _ly_ts(now))
    got = get_today_echo(db, user.id)
    _assert_echo(got, expect, f"enabled={enabled} now={hour:02d}:{minute:02d} quiet={start}-{end}")
    if expect:
        assert got["content_id"] == c.id


@pytest.mark.parametrize(
    "max_per_day,prior_respond,prior_dismiss,expect",
    [
        pytest.param(0, 0, 0, False, id="max_per_day等于0禁用"),
        pytest.param(1, 0, 0, True, id="max_per_day等于1首次放行"),
        pytest.param(1, 1, 0, False, id="max_per_day等于1已有respond拦截"),
        pytest.param(2, 0, 0, True, id="max_per_day等于2首次仍可展示"),
        pytest.param(2, 1, 0, False, id="上限2但唯一索引仍只允许每用户每天一条respond"),
        pytest.param(1, 0, 1, True, id="dismiss行不占用每日名额"),
    ],
)
def test_gate3_daily_limit_matrix(
    db_user, monkeypatch, max_per_day, prior_respond, prior_dismiss, expect
):
    """闸3 边界矩阵：max_per_day=0/1/2 与既有 respond/dismiss 历史交织；
    特别验证 DB 侧部分唯一索引 uq_echo_history_daily 是 daily≤1 的最终兜底
    （即使把上限配成 2，同日第二次插入仍被 IntegrityError 吃掉）。"""
    now = _freeze(monkeypatch, datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ))
    db, user = db_user
    _put_pref(db, user.id, max_per_day=max_per_day)
    for i in range(prior_respond):
        _history(db, user.id, action="respond", shown_date=now.date(), fingerprint=f"prior-r-{i}")
    for i in range(prior_dismiss):
        _history(db, user.id, action="dismiss", shown_date=now.date(), fingerprint=f"prior-d-{i}")
    _content(db, user.id, _ly_ts(now))
    _assert_echo(get_today_echo(db, user.id), expect,
                 f"max={max_per_day} respond={prior_respond} dismiss={prior_dismiss}")


# =========================================================== 闸4：角色可见（占位）
def test_gate4_role_visibility_is_not_implemented(db_user, monkeypatch):
    """闸4 角色可见本期按 EchoPort 约定留占位：实现不得假装做了角色判定；
    即便外部传入非 owner 角色语义，写路径仍按本人端放行（缺省形态）。"""
    now = _freeze(monkeypatch, datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ))
    db, user = db_user
    _put_pref(db, user.id, quiet_start="00:00", quiet_end="00:00")
    c = _content(db, user.id, _ly_ts(now), text="仅本人可见的回忆")
    got = get_today_echo(db, user.id)
    assert got is not None and got["content_id"] == c.id, "闸4 未实现时不应影响本人端回响"


# =========================================================== 闸5：单条有效性
@pytest.mark.parametrize(
    "spec,expect",
    [
        pytest.param({"text": "普通文字回忆"}, True, id="纯文本有效"),
        pytest.param({"text": "", "cos_key": "photos/a.jpg"}, True, id="空文本但有cos原图有效"),
        pytest.param({"text": "", "thumbnail_key": "thumbs/a.jpg"}, True, id="空文本但有缩略图有效"),
        pytest.param({"text": "", "cos_key": None, "thumbnail_key": None}, False, id="空文本且无媒体无效"),
        pytest.param({"text": "   ", "cos_key": None}, False, id="纯空白文本strip后无效"),
        pytest.param({"text": "有文本", "taken_at_none": True}, False, id="有文本但taken_at为None"),
        pytest.param({"text": "普通文字回忆", "deleted": True}, False, id="deleted_at软删内容被alive_clause排除"),
        pytest.param({"text": "普通文字回忆", "sensitive_status": "敏感"}, False, id="内容敏感标记拦截"),
        pytest.param({"text": "", "cos_key": "voice/a.mp3", "content_type": "voice"}, True, id="语音无文本有媒体有效"),
        pytest.param(
            {"text": "照片描述", "content_type": "photo", "cos_key": "photos/p.jpg"},
            True, id="照片带描述有效",
        ),
    ],
)
def test_gate5_candidate_validity_matrix(db_user, monkeypatch, spec, expect):
    """闸5 有效性矩阵：真实 contents 行覆盖空文本/有图、空白文本、taken_at=None、
    deleted_at 软删、敏感标记、photo/voice 媒体型候选，确保 SQL 过滤与纯判定双重生效。"""
    now = _freeze(monkeypatch, datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ))
    db, user = db_user
    _put_pref(db, user.id, quiet_start="00:00", quiet_end="00:00")
    taken = None if spec.get("taken_at_none") else _ly_ts(now)
    c = _content(
        db,
        user.id,
        taken,
        text=spec.get("text", "去年今日的普通回忆"),
        cos_key=spec.get("cos_key"),
        thumbnail_key=spec.get("thumbnail_key"),
        content_type=spec.get("content_type", "text"),
        sensitive_status=spec.get("sensitive_status", "正常"),
    )
    if spec.get("deleted"):
        c.deleted_at = now - timedelta(days=1)
        db.commit()
    _assert_echo(get_today_echo(db, user.id), expect, f"candidate spec={spec}")


# =========================================================== 闸6 / 闸8：冷却与划掉
@pytest.mark.parametrize(
    "action,days_ago,fp_match,expect",
    [
        pytest.param("respond", 29, True, False, id="冷却29天内拦截"),
        pytest.param("respond", 30, True, False, id="冷却30天整边界仍拦截"),
        pytest.param("respond", 31, True, True, id="冷却31天后重新可展示"),
        pytest.param("dismiss", 100, True, False, id="划掉永久排除百日之后仍拦截"),
        pytest.param("dismiss", 0, True, False, id="当天划掉即排除"),
        pytest.param("respond", 3, False, True, id="不同指纹不受他人冷却影响"),
        pytest.param("dismiss", 3, False, True, id="不同指纹不受划掉影响"),
        pytest.param("suppressed", 3, True, True, id="suppressed记录不产生冷却"),
    ],
)
def test_gate6_8_cooldown_and_dismiss_matrix(
    db_user, monkeypatch, action, days_ago, fp_match, expect
):
    """闸6/闸8 状态源矩阵：真实 EchoHistory 交织 respond 冷却（30 天边界含当日）、
    dismiss 永久排除、suppressed 不参与冷却，以及指纹匹配/不匹配四种组合。"""
    now = _freeze(monkeypatch, datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ))
    db, user = db_user
    _put_pref(db, user.id, quiet_start="00:00", quiet_end="00:00")
    c = _content(db, user.id, _ly_ts(now), text="冷却与划掉测试")
    fp = echo_svc._fingerprint(c.id)
    _history(
        db,
        user.id,
        action=action,
        shown_date=now.date() - timedelta(days=days_ago),
        fingerprint=fp if fp_match else "someone-else-fp",
    )
    got = get_today_echo(db, user.id)
    _assert_echo(got, expect, f"action={action} days_ago={days_ago} fp_match={fp_match}")


# =========================================================== 闸7：精确去年今日
@pytest.mark.parametrize(
    "now_iso,content_iso,expect",
    [
        pytest.param("2026-06-15T12:00:00+08:00", "2025-06-15T10:00:00+08:00", True, id="正常去年今日命中"),
        pytest.param("2026-06-15T12:00:00+08:00", "2024-06-15T10:00:00+08:00", False, id="前年同日按精确年份排除"),
        pytest.param("2024-02-29T12:00:00+08:00", "2023-02-28T10:00:00+08:00", True, id="闰日2/29退化命中2/28"),
        pytest.param("2024-02-29T12:00:00+08:00", "2023-03-01T10:00:00+08:00", False, id="闰日不应命中3/1"),
        pytest.param("2025-02-28T12:00:00+08:00", "2024-02-28T10:00:00+08:00", True, id="平年2/28命中去年闰年2/28"),
        pytest.param("2026-01-01T12:00:00+08:00", "2025-01-01T10:00:00+08:00", True, id="跨年元旦命中去年元旦"),
        pytest.param("2026-01-01T12:00:00+08:00", "2025-12-31T23:30:00+08:00", False, id="跨年不误算去年12/31"),
        pytest.param("2026-01-02T00:00:00+08:00", "2025-01-02T00:00:00+08:00", True, id="00:00本地日界命中去年同刻"),
        pytest.param("2026-01-02T00:30:00+08:00", "2025-01-02T00:30:00+08:00", True, id="本地00:30虽UTC为前日仍命中"),
    ],
)
def test_gate7_exact_anniversary_matrix(db_user, monkeypatch, now_iso, content_iso, expect):
    """闸7 日期矩阵：闰日 2/29、跨年、00:00 本地日界；内容以 +08:00 落库
    （PG timestamptz 实际存成前一日 16:00Z），验证读回后按应用本地时区取 date。"""
    _freeze(monkeypatch, datetime.fromisoformat(now_iso))
    db, user = db_user
    _put_pref(db, user.id, quiet_start="00:00", quiet_end="00:00")
    c = _content(db, user.id, datetime.fromisoformat(content_iso), text="闰日跨年日界")
    got = get_today_echo(db, user.id)
    _assert_echo(got, expect, f"now={now_iso} content={content_iso}")
    if expect:
        assert got["content_id"] == c.id


@pytest.mark.parametrize(
    "tz_name,now_ymdhm,content_ymdhm",
    [
        pytest.param("Asia/Shanghai", (2026, 3, 9, 12, 0), (2025, 3, 9, 10, 0), id="上海固定+08"),
        pytest.param("America/New_York", (2026, 3, 9, 12, 0), (2025, 3, 9, 10, 0), id="纽约DST春季跳变后"),
        pytest.param("America/New_York", (2026, 11, 2, 12, 0), (2025, 11, 2, 10, 0), id="纽约DST秋季回拨当日"),
        pytest.param("Asia/Kathmandu", (2026, 6, 15, 12, 0), (2025, 6, 15, 10, 0), id="尼泊尔非整点+05:45"),
    ],
)
def test_gate7_dst_and_timezone_offset(db_user, monkeypatch, tz_name, now_ymdhm, content_ymdhm):
    """闸7 时区/DST：用 ZoneInfo（含 DST 规则）而非固定偏移冻结时间，
    内容 taken_at 以同一 tz 落库，验证 timestamptz 读回后 astimezone 再取 date 的口径；
    覆盖春跳、秋回拨、以及 +05:45 非整点偏移。"""
    tz = ZoneInfo(tz_name)
    _freeze(monkeypatch, datetime(*now_ymdhm, tzinfo=tz))
    db, user = db_user
    _put_pref(db, user.id, quiet_start="00:00", quiet_end="00:00")
    c = _content(db, user.id, datetime(*content_ymdhm, tzinfo=tz), text="跨时区回响")
    got = get_today_echo(db, user.id)
    assert got is not None and got["content_id"] == c.id, (
        f"tz={tz_name} now={now_ymdhm} content={content_ymdhm} 应命中，实际 {got!r}"
    )


# =========================================================== 闸8：画像敏感五级处置
@pytest.mark.parametrize(
    "disposition,expect",
    [
        pytest.param("forbid", False, id="forbid命中跳过"),
        pytest.param("caution", False, id="caution命中跳过"),
        pytest.param("review", False, id="review命中跳过"),
        pytest.param("allow", True, id="allow放行"),
        pytest.param("mention", True, id="mention放行"),
    ],
)
def test_gate8_profile_disposition_matrix(db_user, monkeypatch, disposition, expect):
    """闸8 画像 L1：真实 ProfileSensitive（密文 topic + hash 盲索引）五行处置矩阵，
    forbid/caution/review 跳过不重提，allow/mention 放行。"""
    now = _freeze(monkeypatch, datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ))
    db, user = db_user
    _profile(db, user.id, "前女友", disposition)
    c = _content(db, user.id, _ly_ts(now), text="那天在前女友楼下等她")
    got = get_today_echo(db, user.id)
    _assert_echo(got, expect, f"profile disposition={disposition}")
    if expect:
        assert got["content_id"] == c.id


@pytest.mark.parametrize(
    "text,sensitive_status,expect",
    [
        pytest.param("普通的一次散步", "正常", True, id="干净文本放行"),
        pytest.param("支持法轮功的言论", "正常", False, id="LLM规则层敏感词拒发"),
        pytest.param("普通的一次散步", "敏感", False, id="入库敏感标记拦截"),
        pytest.param("", "正常", True, id="空文本有图时跳过LLM仍放行"),
    ],
)
def test_gate8_sensitive_content_matrix(db_user, monkeypatch, text, sensitive_status, expect):
    """闸8 敏感双查：真实 contents 的 sensitive_status 与 moderate 规则预检
    （对未标记但命中硬规则词的文本拒发）；空文本有媒体时不调用 LLM。"""
    now = _freeze(monkeypatch, datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ))
    db, user = db_user
    _put_pref(db, user.id, quiet_start="00:00", quiet_end="00:00")
    kwargs = {"text": text, "sensitive_status": sensitive_status}
    if not text:
        kwargs["cos_key"] = "photos/no-text.jpg"
    _content(db, user.id, _ly_ts(now), **kwargs)
    _assert_echo(get_today_echo(db, user.id), expect,
                 f"text={text!r} sensitive_status={sensitive_status}")


def test_multi_candidate_first_llm_rejected_must_not_fallback(db_user, monkeypatch):
    """多候选安全：taken_at 更晚的首候选文本命中 LLM 拒发时，按 echo.py 注释口径
    「首候选 LLM 未过 → 不再回退未验证候选」，应返回 None。
    实现现状：llm_checked 在检查前就置 True，continue 后第二候选跳过 LLM 被直接回响
    ——本用例故意钉住产品预期，用于暴露该隐藏缺陷。"""
    now = _freeze(monkeypatch, datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ))
    db, user = db_user
    _put_pref(db, user.id, quiet_start="00:00", quiet_end="00:00")
    _content(db, user.id, _ly_ts(now, hour=15), text="支持法轮功的言论", sensitive_status="正常")
    _content(db, user.id, _ly_ts(now, hour=10), text="普通的一次散步", sensitive_status="正常")
    got = get_today_echo(db, user.id)
    assert got is None, (
        "首候选被 LLM 拒发后不应把未做 LLM 校验的第二候选回响出去，实际返回 "
        f"{got!r}（content_id={got.get('content_id') if got else None}）"
    )


def test_gate8_echo_exclusion_word_is_honored(db_user, monkeypatch):
    """闸8 用户排除：真实落一行 EchoExclusion(kind='word', word='前女友') 后，
    含该词的去年今日内容不应被回响。实现现状：echo 服务从未读取 EchoExclusion
    ——本用例故意钉住用户可控配置的预期语义，用于暴露该能力缺口。"""
    now = _freeze(monkeypatch, datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ))
    db, user = db_user
    _put_pref(db, user.id, quiet_start="00:00", quiet_end="00:00")
    db.add(EchoExclusion(user_id=user.id, kind="word", word="前女友"))
    db.commit()
    c = _content(db, user.id, _ly_ts(now), text="那天在前女友楼下等她")
    got = get_today_echo(db, user.id)
    assert got is None, (
        f"echo_exclusion 的 word 排除应拦截候选 {c.id}，实际返回 {got!r}"
    )


# =========================================================== 并发 / 用户隔离 / peek
def test_daily_unique_index_blocks_concurrent_respond(db_user):
    """闸3 竞态兜底：两个独立 Session 并发插入同一 (user_id, shown_date) 的 respond，
    部分唯一索引 uq_echo_history_daily 必须让其中一条吃 IntegrityError，库中恰好一条。"""
    db, user = db_user
    today = echo_svc._local_now().date()
    barrier = threading.Barrier(2)
    results: dict[int, str] = {}

    def worker(i: int) -> None:
        from app.db.session import SessionLocal

        session = SessionLocal()
        try:
            barrier.wait(timeout=10)
            session.add(
                EchoHistory(
                    user_id=user.id,
                    action="respond",
                    shown_date=today,
                    fingerprint=f"race-fp-{i}",
                )
            )
            session.commit()
            results[i] = "ok"
        except IntegrityError:
            session.rollback()
            results[i] = "conflict"
        except Exception as exc:  # noqa: BLE001 - 记录任何逃逸异常
            session.rollback()
            results[i] = repr(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    assert sorted(results.values()) == ["conflict", "ok"], f"并发结果异常：{results}"
    n = db.scalar(
        select(func.count())
        .select_from(EchoHistory)
        .where(
            EchoHistory.user_id == user.id,
            EchoHistory.shown_date == today,
            EchoHistory.action == "respond",
        )
    )
    assert n == 1, f"同日 respond 应恰好一条，实际 {n}"


def test_user_isolation_preference(db_user, make_user, cleanup_user, monkeypatch):
    """用户隔离（设置）：A 用户关闭授权 + 上限0，B 用户仍按自己的默认设置拿到回响；
    验证 echo_preference 严格按 user_id 主键隔离，不串配置。"""
    now = _freeze(monkeypatch, datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ))
    db, user_a = db_user
    user_b = make_user(db, "echo-iso-pref")
    try:
        _put_pref(db, user_a.id, enabled=False, max_per_day=0)
        _put_pref(db, user_b.id, enabled=True, max_per_day=1)
        _content(db, user_a.id, _ly_ts(now), text="A的回忆")
        c_b = _content(db, user_b.id, _ly_ts(now), text="B的回忆")

        assert get_today_echo(db, user_a.id) is None
        got_b = get_today_echo(db, user_b.id)
        assert got_b is not None and got_b["content_id"] == c_b.id
    finally:
        cleanup_user(db, user_b.id)
        db.delete(user_b)
        db.commit()


def test_user_isolation_profile_sensitive(db_user, make_user, cleanup_user, monkeypatch):
    """用户隔离（画像敏感）：A 用户对「前女友」设 forbid，仅 A 被跳过；
    B 用户同样文本仍可回响，验证密文 topic + blind index 按 user_id 隔离。"""
    now = _freeze(monkeypatch, datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ))
    db, user_a = db_user
    user_b = make_user(db, "echo-iso-profile")
    try:
        _profile(db, user_a.id, "前女友", "forbid")
        _content(db, user_a.id, _ly_ts(now), text="前女友的回忆")
        c_b = _content(db, user_b.id, _ly_ts(now), text="前女友的回忆")

        assert get_today_echo(db, user_a.id) is None
        got_b = get_today_echo(db, user_b.id)
        assert got_b is not None and got_b["content_id"] == c_b.id
    finally:
        cleanup_user(db, user_b.id)
        db.delete(user_b)
        db.commit()


@pytest.mark.parametrize(
    "enabled,max_per_day,expect",
    [
        pytest.param(True, 1, True, id="正常时peek与get都返回候选"),
        pytest.param(False, 1, False, id="关闭授权时peek与get都返回None"),
        pytest.param(True, 0, False, id="上限0时peek与get都返回None"),
    ],
)
def test_peek_and_get_gate_consistency(db_user, monkeypatch, enabled, max_per_day, expect):
    """只读 peek 与写路径 get 的闸1/闸3 口径一致性：peek 不写 EchoHistory/消息，
    但同样受授权与每日上限约束；用于防止 Agent 读候选旁路八闸。"""
    now = _freeze(monkeypatch, datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ))
    db, user = db_user
    _put_pref(db, user.id, enabled=enabled, max_per_day=max_per_day,
              quiet_start="00:00", quiet_end="00:00")
    c = _content(db, user.id, _ly_ts(now), text="peek一致性")
    peeked = peek_today_echo(db, user.id)
    _assert_echo(peeked, expect, "peek")
    got = get_today_echo(db, user.id)
    _assert_echo(got, expect, "get")
    if expect:
        assert peeked["content_id"] == c.id and got["content_id"] == c.id
        n = db.scalar(
            select(func.count()).select_from(EchoHistory).where(EchoHistory.user_id == user.id)
        )
        assert n == 1, "peek 只读不应写历史，get 写一条"


# --------------------------------------------------------------------------- 审判修复回归（P0/P1）
def test_llm_mask_uses_masked_text_not_raw(db_user, monkeypatch):
    """P0：护栏 action=mask 时必须展示打码文本，不能把原手机号/身份证推给用户。"""
    now = _freeze(monkeypatch, datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ))
    db, user = db_user
    _put_pref(db, user.id, quiet_start="00:00", quiet_end="00:00")
    raw = "去年今天联系 13812345678 办手续"
    masked = "去年今天联系 138****5678 办手续"
    _content(db, user.id, _ly_ts(now), text=raw)

    monkeypatch.setattr(
        echo_svc, "_screen_text",
        lambda text: masked if raw in text else text,
    )
    got = get_today_echo(db, user.id)
    assert got is not None and got["text"] == masked, "mask 必须下发打码文本"
    assert raw not in got["text"], "原文不得出现在 API 响应"
    # 消息中心 payload 也必须打码（不能绕过）
    from app.db.models import Message
    msg = db.execute(
        select(Message).where(Message.user_id == user.id, Message.msg_type == "echo")
    ).scalars().one_or_none()
    assert msg is not None and masked in (msg.payload or {}).get("text", "")


def test_peek_screened_same_as_get(db_user, monkeypatch):
    """P0：peek 必须与 get 同样过 LLM 安全闸，Agent 入口不能绕过。"""
    now = _freeze(monkeypatch, datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ))
    db, user = db_user
    _put_pref(db, user.id, quiet_start="00:00", quiet_end="00:00")
    _content(db, user.id, _ly_ts(now), text="语义敏感但规则层未命中")

    monkeypatch.setattr(echo_svc, "_screen_text", lambda text: None)
    got = get_today_echo(db, user.id)
    peeked = peek_today_echo(db, user.id)
    assert got is None and peeked is None, "get/peek 都必须被 LLM 拒发挡住"


def test_echo_exclusion_word_blocks(db_user, monkeypatch):
    """P1：用户显式排除词必须生效（审判前该表从未被读取）。"""
    now = _freeze(monkeypatch, datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ))
    db, user = db_user
    _put_pref(db, user.id, quiet_start="00:00", quiet_end="00:00")
    db.add(EchoExclusion(user_id=user.id, kind="word", word="前女友"))
    db.commit()
    _content(db, user.id, _ly_ts(now), text="那天在前女友楼下等她")
    assert get_today_echo(db, user.id) is None


def test_echo_exclusion_date_range_blocks(db_user, monkeypatch):
    """P1：date_range 覆盖今天时，所有回响候选都被屏蔽。"""
    now = _freeze(monkeypatch, datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ))
    db, user = db_user
    _put_pref(db, user.id, quiet_start="00:00", quiet_end="00:00")
    db.add(EchoExclusion(
        user_id=user.id, kind="date_range",
        date_from=date(2026, 6, 1), date_to=date(2026, 6, 30),
    ))
    db.commit()
    _content(db, user.id, _ly_ts(now), text="排除区间内的记忆")
    assert get_today_echo(db, user.id) is None


def test_dismiss_rejects_foreign_content(db_user, monkeypatch):
    """P1：dismiss 不得划掉他人内容（IDOR），非本人 → 404。"""
    from app.api import deps
    from app.main import app
    from fastapi.testclient import TestClient

    now = _freeze(monkeypatch, datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ))
    db, user = db_user
    other_content = _content(db, "00000000-0000-0000-0000-000000000001", _ly_ts(now), text="别人的")
    client = TestClient(app)
    app.dependency_overrides[deps.get_current_user] = lambda: user
    try:
        r = client.post(f"/api/v1/echo/{other_content.id}/dismiss")
        assert r.status_code == 404, "划掉他人内容必须 404"
    finally:
        app.dependency_overrides.clear()


def test_preference_extra_forbidden_and_error_envelope(db_user):
    """P1：未知字段必须 422，且错误信封含 code/request_id（统一 ApiError）。"""
    from app.api import deps
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    client = TestClient(app)
    app.dependency_overrides[deps.get_current_user] = lambda: user
    try:
        r = client.put("/api/v1/echo/preference", json={"channels": "push"})
        assert r.status_code == 422
        body = r.json()
        assert "code" in body and "request_id" in body, f"统一信封缺失：{body}"
        assert body["code"] == "VALIDATION_ERROR", body
    finally:
        app.dependency_overrides.clear()


def test_dismiss_concurrent_idempotent(db_user, monkeypatch):
    """P1：并发两次 dismiss 只落一条记录（部分唯一索引 uq_echo_history_dismiss 兜底）。"""
    now = _freeze(monkeypatch, datetime(2026, 6, 15, 12, 0, tzinfo=LOCAL_TZ))
    db, user = db_user
    c = _content(db, user.id, _ly_ts(now), text="并发划掉")
    fp = echo_svc._fingerprint(c.id)
    # 直接并发插两条 dismiss，唯一索引必须挡住第二条
    from app.db.session import SessionLocal
    results = []
    barrier = threading.Barrier(2)

    def worker():
        s = SessionLocal()
        try:
            barrier.wait()
            s.add(EchoHistory(user_id=user.id, action="dismiss", fingerprint=fp,
                              shown_date=now.date()))
            s.commit()
            results.append("ok")
        except IntegrityError:
            s.rollback()
            results.append("conflict")
        finally:
            s.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["conflict", "ok"], f"并发结果应为 1 成功 1 冲突：{results}"
    n = db.scalar(
        select(func.count()).select_from(EchoHistory).where(
            EchoHistory.user_id == user.id, EchoHistory.action == "dismiss",
            EchoHistory.fingerprint == fp,
        )
    )
    assert n == 1
