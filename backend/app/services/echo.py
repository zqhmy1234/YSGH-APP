"""回响服务（P2-ECHO · 去年今日）

规则（产品部已确认）：
- 回响 = 去年今日的内容（照片/文字/语音），in-app 展示（非 push）
- 每天 ≤1 条（echo_history.shown_at 同日去重）；划掉（dismiss）不再出现
- 敏感排除（SAF-006/007 双查）：contents.sensitive_status ≠ 正常 跳过 +
  画像级敏感（profile_sensitive）命中跳过
- 指纹：内容 id 稳定派生（跨端同一条）

画像级敏感（B5b FIX-4）：本模块同时提供 profile_sensitive 表的服务函数
（回响 L1 校验 + B1-6 对话式增删查），供 /api/v1/profile/sensitive 使用。
"""
from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.timeutil import local_now
from app.db.models import Content, EchoExclusion, EchoHistory, EchoPreference, ProfileSensitive
from app.services.llm_ops.moderate import verdict_action
from app.services.query_helpers import alive_clause

logger = logging.getLogger("yishu.echo")

# B4 合并八闸（2026-11-24）：六个原意图 + 后端两道安全闸，状态源全部来自 DB。
# 闸4「角色可见」仅留接口占位（EchoPort.role），依赖 B8 家庭域，本期不实现——见 ports/echo.py。
COOLDOWN_DAYS = 30
_DEFAULT_PREFERENCE = {"enabled": True, "quiet_start": "22:00", "quiet_end": "08:00",
                       "max_per_day": 1, "channels": "in_app"}

# 画像 L1 校验：命中这些处置级别 → 回响跳过不重提（产品口径；allow/mention 放行）
PROFILE_BLOCK_DISPOSITIONS = {"forbid", "caution", "review"}
PROFILE_DISPOSITIONS = {"allow", "mention", "caution", "review", "forbid"}


def _fingerprint(content_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"echo:{content_id}"))


def _local_now() -> datetime:
    """本地时间（日界按本地口径：修复审查 MINOR——原按 UTC 日界，本地 0:00-8:00
    会被算到前一天）

    2026-09-25（功能修复波 D04-1）：**改为委托 `app.core.timeutil.local_now()`**。
    本函数原用 `datetime.now().astimezone()`＝**服务器本地**，在 UTC 容器里等于 UTC（修复无效）；
    而事件聚合 L1 又另走 UTC 默认 ⇒ 「同一云侧两套日界」是 P0 缺陷 D04-1 的根因。
    现云侧日界统一到 `APP_LOCAL_TZ`（单一来源，不依赖容器 TZ）。
    """
    return local_now()


# ---------------------------------------------------------------- B4 八闸
def _load_preference(db: Session, user_id: str) -> dict:
    """闸1/2/3 状态源：echo_preference 表。无行时返回建表默认（保持内测前行为）。"""
    row = db.get(EchoPreference, user_id)
    if row is None:
        return dict(_DEFAULT_PREFERENCE)
    return {
        "enabled": bool(row.enabled),
        "quiet_start": row.quiet_start,
        "quiet_end": row.quiet_end,
        "max_per_day": int(row.max_per_day),
        "channels": row.channels,
    }


def _in_quiet_window(now: datetime, start: str, end: str) -> bool:
    """闸2：跨午夜静默判定 start≤t or t<end（口径见合并方案；无现有代码依据，本期拍板）。

    start==end 表示不设静默窗口（供测试与用户关闭静默用）。
    """
    if start == end:
        return False
    try:
        sh, sm = (int(x) for x in start.split(":")[:2])
        eh, em = (int(x) for x in end.split(":")[:2])
    except (ValueError, AttributeError):
        return False
    cur = now.hour * 60 + now.minute
    s, e = sh * 60 + sm, eh * 60 + em
    return s <= e and s <= cur < e or s > e and (cur >= s or cur < e)


def _gate_global(pref: dict, now: datetime) -> str | None:
    """闸1/2：不过返回 suppressed 原因；过返回 None。"""
    if not pref["enabled"]:
        return "disabled"
    if _in_quiet_window(now, pref["quiet_start"], pref["quiet_end"]):
        return "quiet_hours"
    return None


def _daily_shown(db: Session, user_id: str, today: date) -> int:
    """当日已展示条数。用 shown_date 列（与唯一索引同源），不再用 shown_at 推导，
    避免 DB 时钟与应用 APP_LOCAL_TZ 漂移导致同一天两套口径（审判 P2-5）。"""
    return db.scalar(
        select(func.count())
        .select_from(EchoHistory)
        .where(
            EchoHistory.user_id == user_id,
            EchoHistory.shown_date == today,
            EchoHistory.action != "dismiss",
        )
    ) or 0


def _recently_shown_fingerprints(db: Session, user_id: str, now: datetime) -> set[str]:
    """闸6：30 天内展示过的 fingerprint（per-content 冷却，状态源 echo_history）。"""
    cutoff = (now - timedelta(days=COOLDOWN_DAYS)).date()
    rows = db.execute(
        select(EchoHistory.fingerprint).where(
            EchoHistory.user_id == user_id,
            EchoHistory.action == "respond",
            EchoHistory.shown_date >= cutoff,
        )
    ).scalars().all()
    return {r for r in rows if r}


def _candidate_valid(content: Content, fp: str, dismissed: set[str],
                     cooled: set[str], profile_rows, *, expected: date,
                     local_tz, exclusions: tuple[list[str], list[str], bool]) -> bool:
    """闸5/6/7/8 的逐候选纯判定（不写库，不做 LLM）：有效性 + 时机 + 敏感 + 排除。

    expected / local_tz 由调用方在请求开始时算一次传入，避免跨午夜时逐候选重算自相矛盾。
    闸4 角色可见：本期留接口不实现（B8 依赖），故此处不判 role。
    闸8 LLM：由调用方在通过本判定后统一 _screen_text（get/peek 共用）。
    """
    # 闸5 单条有效性：alive_clause 已在查询层；此处补文本/媒体非空 + taken_at 有效
    if not content.taken_at:
        return False
    if not (content.text or "").strip() and not (content.cos_key or content.thumbnail_key):
        return False
    # 闸7 时机：精确去年今日（含年份）。PG timestamptz 读出可能是 UTC tzinfo，
    # 必须先转应用本地时区再取 date，否则 00:00+08:00 存成 16:00Z 会被误判成前一天。
    local_taken = content.taken_at.astimezone(local_tz)
    if local_taken.date() != expected:
        return False
    # 闸6 冷却 + 闸8 dismiss 排除
    if fp in cooled or fp in dismissed:
        return False
    # 闸8 敏感：画像 forbid/caution/review + 内容标记
    text = content.text or ""
    if _profile_hit(text, profile_rows):
        return False
    if content.sensitive_status and content.sensitive_status != "正常":
        return False
    # 闸8 用户显式排除（echo_exclusion）：词 / 人物 / 日期段
    words, people, date_blocked = exclusions
    if date_blocked:
        return False
    if any(w in text for w in words) or any(p in text for p in people):
        return False
    return True


def _last_year_date(today: date) -> date:
    try:
        return today.replace(year=today.year - 1)
    except ValueError:  # 闰日
        return today.replace(year=today.year - 1, day=28)


def _screen_text(text: str) -> str | None:
    """闸8 LLM 护栏（get/peek 共用）：返回可展示文本；拒发返回 None。

    按 moderate.py 的唯一解读函数 verdict_action 处理三态：
      - reject → None（不回退到未检候选，调用方须终止整批）
      - mask   → masked_text（打码后展示，原内容不外发）
      - allow  → 原文
    空文本（纯照片/语音）无内容可检，直接返回原文。
    """
    if not text or not text.strip():
        return text
    from app.services.llm_ops.base import moderate

    verdict = moderate(text)
    action = verdict_action(verdict)
    if action == "reject":
        return None
    if action == "mask":
        return verdict.get("masked_text") or text  # 缺 masked_text 时退回原文有风险，但护栏契约保证恒带
    return text


def _select_candidate(db: Session, user_id: str, *, today: date, now_local: datetime,
                      pref: dict) -> tuple[Content, str, str] | None:
    """get/peek 共用的候选筛选（八闸全判定，含 LLM）。

    返回 (content, fingerprint, display_text)；无候选或全被拒 → None。
    不写库——展示记录与消息由 get_today_echo 在本函数返回后落。
    LLM 语义：首候选拒发 → 整批 None（不回退到未检候选）；mask → 用打码文本。
    """
    expected = _last_year_date(today)           # 请求开始算一次，不逐候选重算
    local_tz = now_local.tzinfo
    day_start = datetime.combine(expected, time.min, tzinfo=local_tz)
    day_end = day_start + timedelta(days=1)
    rows = db.execute(
        select(Content)
        .where(
            Content.user_id == user_id,
            alive_clause(Content),
            Content.taken_at >= day_start,
            Content.taken_at < day_end,
        )
        .order_by(Content.taken_at.desc())
        .limit(20)
    ).scalars().all()
    if not rows:
        return None
    dismissed = set(
        db.execute(
            select(EchoHistory.fingerprint).where(
                EchoHistory.user_id == user_id, EchoHistory.action == "dismiss"
            )
        ).scalars().all()
    )
    cooled = _recently_shown_fingerprints(db, user_id, now_local)
    profile_rows = db.execute(
        select(ProfileSensitive).where(ProfileSensitive.user_id == user_id)
    ).scalars().all()
    exclusions = _load_exclusions(db, user_id, today)
    # B6 保险箱：锁住的内容不进回响候选（访问入口过滤）
    from app.services.vault import locked_content_ids
    vaulted = locked_content_ids(db, user_id)

    for content in rows:
        fp = _fingerprint(content.id)
        if content.id in vaulted:
            continue
        if not _candidate_valid(
            content, fp, dismissed, cooled, profile_rows,
            expected=expected, local_tz=local_tz, exclusions=exclusions,
        ):
            continue
        # 闸8 LLM：首个通过便宜检查的候选必检；拒发→整批 None（不回退到未检候选）
        # 后续候选也逐个检（成本可控：通常首批即命中；绝不因首候选被拒而放行未检内容）。
        raw_text = content.text or ""
        display = _screen_text(raw_text)
        if display is None:
            return None
        return content, fp, display
    return None


def _load_exclusions(db: Session, user_id: str, today: date):
    """闸8 用户显式排除：person(人名) / word(词) / date_range(日期段) 三类。

    返回 (words, people, blocked_date)，供逐候选过滤；DB 直写旧数据时容错跳过非法行。
    """
    words: list[str] = []
    people: list[str] = []
    blocked = False
    rows = db.execute(
        select(EchoExclusion).where(EchoExclusion.user_id == user_id)
    ).scalars().all()
    for r in rows:
        if r.kind == "word" and (r.word or "").strip():
            words.append(r.word.strip())
        elif r.kind == "person" and (r.word or "").strip():
            people.append(r.word.strip())
        elif r.kind == "date_range":
            try:
                lo = r.date_from.date() if hasattr(r.date_from, "date") else r.date_from
                hi = r.date_to.date() if hasattr(r.date_to, "date") else r.date_to
                if lo and hi and lo <= today <= hi:
                    blocked = True
                elif lo and not hi and today >= lo:
                    blocked = True
                elif hi and not lo and today <= hi:
                    blocked = True
            except (TypeError, AttributeError):
                continue
    return words, people, blocked


def profile_sensitive_blocked(db: Session, user_id: str, text: str) -> bool:
    """画像 L1 校验（B5b FIX-4）：查 profile_sensitive，命中 topic 且处置级别 ∈
    {forbid, caution, review} → 跳过不重提（allow/mention 放行）。

    画像级敏感永不过期（locked 显式标记/默认 forbid 均无有效期逻辑）——
    与事件级（≥3 次提及降级）不同，画像行不存在降级路径。
    """
    if not text or not text.strip():
        return False
    rows = db.execute(
        select(ProfileSensitive).where(ProfileSensitive.user_id == user_id)
    ).scalars().all()
    return _profile_hit(text, rows)


def _profile_hit(text: str, rows) -> bool:
    """命中画像敏感处置（forbid/caution/review）——rows 为已加载 ProfileSensitive 行

    S6-7：独立出纯函数，供 get_today_echo 一次加载复用（避免逐候选 N 次查询）。
    """
    for row in rows:
        topic = (row.topic or "").strip()
        if not topic:
            continue
        if topic in text and row.disposition in PROFILE_BLOCK_DISPOSITIONS:
            return True
    return False


def upsert_profile_sensitive(
    db: Session,
    user_id: str,
    topic: str,
    disposition: str = "forbid",
    evidence: list | None = None,
    locked: bool = False,
) -> ProfileSensitive:
    """画像敏感增/改：(user_id, topic_hash) 原子查重；数据库不写明文。

    R2#13 竞态修复：原 SELECT 查重 → INSERT 有并发窗口（同 (user_id, topic) 双插
    败者 IntegrityError 500）——唯一约束 profile_sensitive_user_id_topic_hash_key 兜底，
    但未捕获异常。现改 ON CONFLICT DO UPDATE 原子 upsert（单语句，无竞态窗口）。

    locked=True 为用户显式标记（永不过期语义强化）；返回值即最新行。
    """
    if disposition not in PROFILE_DISPOSITIONS:
        raise ValueError(f"disposition 非法：{disposition}（可选 {sorted(PROFILE_DISPOSITIONS)}）")
    topic = topic.strip()
    if not topic:
        raise ValueError("topic 不能为空")
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.services.profile_sensitive_crypto import encrypt_topic

    topic_enc, topic_hash = encrypt_topic(topic)
    evidence_list = list(evidence) if evidence else []
    stmt = (
        pg_insert(ProfileSensitive)
        .values(
            user_id=user_id, topic_enc=topic_enc, topic_hash=topic_hash,
            disposition=disposition,
            evidence=evidence_list, locked=locked,
        )
        .on_conflict_do_update(
            index_elements=[ProfileSensitive.user_id, ProfileSensitive.topic_hash],
            set_={
                "disposition": disposition,
                "evidence": evidence_list,
                "locked": locked,
                "updated_at": func.now(),
            },
        )
    )
    db.execute(stmt)
    db.commit()
    # 强制过期 identity-map 缓存：Core 写不会更新 ORM 对象（expire_on_commit=False），
    # 不 expire 则重查返回旧值（如 disposition 仍是插入时的值）
    db.expire_all()
    return db.execute(
        select(ProfileSensitive).where(
            ProfileSensitive.user_id == user_id, ProfileSensitive.topic_hash == topic_hash
        )
    ).scalar_one()


def delete_profile_sensitive(db: Session, user_id: str, topic: str) -> bool:
    """画像敏感删（B1-6）：删除该用户该话题；不存在返回 False"""
    from app.services.profile_sensitive_crypto import topic_digest

    digest = topic_digest(topic)
    row = db.execute(
        select(ProfileSensitive).where(
            ProfileSensitive.user_id == user_id, ProfileSensitive.topic_hash == digest
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def list_profile_sensitive(
    db: Session, user_id: str, disposition: str | None = None
) -> list[ProfileSensitive]:
    """画像敏感查（B1-6）：列出该用户全部话题（可按处置级别过滤），按更新时间倒序"""
    q = select(ProfileSensitive).where(ProfileSensitive.user_id == user_id)
    if disposition:
        if disposition not in PROFILE_DISPOSITIONS:
            raise ValueError(f"disposition 非法：{disposition}")
        q = q.where(ProfileSensitive.disposition == disposition)
    q = q.order_by(ProfileSensitive.updated_at.desc())
    return db.execute(q).scalars().all()


def _is_sensitive(db: Session, content: Content, profile_rows=None) -> bool:
    """敏感双查（SAF-006/007；用户 2026-08-20 拍板：已有敏感标记 + LLM 检测）

    第零查（B5b FIX-4 前置画像 L1 校验）：profile_sensitive 命中 forbid/caution/review
    → 跳过不重提（画像级永不过期，无降级路径）
    第一查：内容入库时的敏感标记（contents.sensitive_status ≠ 正常）
    第二查：出包前 LLM 检测（llm_ops.base.moderate：规则预检 + 百炼护栏，fail-safe）

    profile_rows：可选已加载 ProfileSensitive 行（S6-7 get_today_echo 一次加载复用）。
    """
    text = content.text or ""
    if profile_rows is not None:
        if _profile_hit(text, profile_rows):
            return True
    elif profile_sensitive_blocked(db, str(content.user_id), text):
        return True
    if content.sensitive_status and content.sensitive_status != "正常":
        return True
    # 第二查：LLM 检测（统一用 verdict_action 解读三态，不用 .get("pass", True) fail-open）
    if text.strip():
        from app.services.llm_ops.base import moderate

        verdict = moderate(text)
        action = verdict_action(verdict)
        if action in ("reject", "mask"):  # mask 也不可按原文展示
            return True
    return False


def peek_today_echo(db: Session, user_id: str) -> dict | None:
    """Read-only 候选：复用 ``get_today_echo`` 的筛选口径，但不写 EchoHistory/消息。

    Agent 的 ECHO 意图只能读候选（是否真正展示由用户端决定），所以不能直接
    调 ``get_today_echo``——那会写展示记录并消耗当天的 1 条名额。
    """
    now_local = _local_now()
    today = now_local.date()
    pref = _load_preference(db, user_id)
    # 闸1/2/3：授权 + 静默 + 每日上限（peek 只读，反映当天名额；不落抑制记录）
    if _gate_global(pref, now_local):
        return None
    if pref["max_per_day"] <= 0 or _daily_shown(db, user_id, today) >= pref["max_per_day"]:
        return None
    picked = _select_candidate(db, user_id, today=today, now_local=now_local, pref=pref)
    if picked is None:
        return None
    content, fp, display_text = picked
    return {
        "content_id": content.id,
        "content_type": content.content_type,
        "text": display_text,
        "taken_at": content.taken_at.isoformat() if content.taken_at else None,
        "place": content.place,
        "echo_date": today.isoformat(),
        "fingerprint": fp,
    }


def get_today_echo(db: Session, user_id: str) -> dict | None:
    """去年今日回响（B4 合并八闸；无内容/已展示/敏感/设置关闭 → None）"""
    now_local = _local_now()
    today = now_local.date()
    pref = _load_preference(db, user_id)
    # 闸1/2：授权 + 静默
    if _gate_global(pref, now_local):
        return None
    # 闸3：每日上限（读 max_per_day，替掉写死的 ECHO_DAILY_LIMIT）
    if pref["max_per_day"] <= 0 or _daily_shown(db, user_id, today) >= pref["max_per_day"]:
        return None

    # 八闸候选筛选（含 LLM，get/peek 共用同一实现）：返回即已过全部闸
    picked = _select_candidate(db, user_id, today=today, now_local=now_local, pref=pref)
    if picked is None:
        return None
    content, fp, display_text = picked

    # 记录展示（唯一索引 uq_echo_history_daily 兜底并发，插入冲突 → 按已展示处理）
    try:
        db.add(
            EchoHistory(
                user_id=user_id,
                event_id=None,
                action="respond",
                fingerprint=fp,
                shown_date=today,
            )
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    # R9 批次3：回响首次展示同步建发 in-app echo 消息，payload=回响快照
    snapshot = {
        "content_id": content.id,
        "content_type": content.content_type,
        "text": display_text,          # mask 时用打码文本，原文不外发
        "taken_at": content.taken_at.isoformat() if content.taken_at else None,
        "place": content.place,
        "echo_date": today.isoformat(),
        "fingerprint": fp,
    }
    _create_echo_message(db, user_id, snapshot)
    return snapshot


def _create_echo_message(db: Session, user_id: str, snapshot: dict) -> None:
    """建发 in-app echo 消息（payload=回响快照，R9 批次3）

    事务边界：EchoHistory 已由调用方 commit；本函数 create_message(flush)+commit
    独立落库。消息建发失败不阻断回响本体（回响查询是主产物），但 logger.error
    显式记录原因——违反「结果不允许静默」军规的兜底是日志而非吞异常。
    """
    text = (snapshot.get("text") or "").strip()
    ctype = snapshot.get("content_type") or "text"
    body_hint = {
        "photo": "去年今天的一张照片",
        "voice": "去年今天的一段声音",
    }.get(ctype, "去年今天的一段文字")
    body = (text[:40] + "…") if len(text) > 40 else (text or body_hint)
    try:
        from app.services.notify import create_message

        create_message(
            db,
            user_id,
            channel="in_app",
            msg_type="echo",
            title="时间胶囊 · 去年今天",
            body=body,
            payload=snapshot,
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.error(
            "echo 消息建发失败（回响本体不受影响）user=%s content=%s",
            user_id,
            snapshot.get("content_id"),
            exc_info=True,
        )


def dismiss_echo(db: Session, user_id: str, content_id: str) -> None:
    """划掉：不再出现（B5-a 回响交互；按 fingerprint 幂等）"""
    db.add(
        EchoHistory(
            user_id=user_id,
            event_id=None,
            action="dismiss",
            fingerprint=_fingerprint(content_id),
            shown_date=_local_now().date(),
        )
    )
    db.commit()
