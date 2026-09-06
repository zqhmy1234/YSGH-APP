"""数据导出服务（US-42 · 卡E03 W3 恢复入库；原实现见 stash 49bed24 考古）

聚合用户全量元数据（contents + events + profile + corrections），拍板 D1：
**不含图片二进制**——contents 的 url 字段为存储 URL 引用（cos_key/thumbnail_key），
可回溯原图但不包含图片数据本身。

安全：
  - 用户隔离：全部查询按 user_id 过滤（services 层兜底，与 API 层鉴权双保险）
  - 分页防护：contents/events/corrections 按 id 键集分块取数（_CHUNK 一次），
    不一次全表加载；每类上限 EXPORT_MAX_PER_TYPE=2000，超出截断并标记 truncated=True
  - 隐私：导出响应不含原始 user_id，以 user_id_hashed（sha256 前缀）替代
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Content,
    CorrectionLog,
    Event,
    EventItem,
    ProfileSensitive,
    UserProfile,
)

# 每块取数上限（分页防护：单查询不一次全表）
_CHUNK = 500
# 每类导出上限（超出截断并置 truncated=True，防超大库单响应失控）
EXPORT_MAX_PER_TYPE = 2000


def _collect_capped(
    db: Session, model, user_id: str, cap: int | None = None
) -> tuple[list, bool]:
    """按 id 键集分页取用户行（增量游标 id > last_id，PG 端比较，UUID/BigInt 通用）。

    cap 缺省取模块常量 EXPORT_MAX_PER_TYPE（调用期求值，便于测试 monkeypatch 收窄）。
    返回 (rows[:cap], truncated)——取到 cap 后仍尝试再查一块以探测是否超限。
    """
    if cap is None:
        cap = EXPORT_MAX_PER_TYPE
    rows: list = []
    last_id = None
    while len(rows) <= cap:  # 多取一块用于探测截断
        stmt = select(model).where(model.user_id == user_id).order_by(model.id)
        if last_id is not None:
            stmt = stmt.where(model.id > last_id)
        batch = db.execute(stmt.limit(_CHUNK)).scalars().all()
        if not batch:
            break
        rows.extend(batch)
        last_id = batch[-1].id
    return rows[:cap], len(rows) > cap


def _content_out(c: Content) -> dict:
    return {
        "content_id": str(c.id),
        "content_type": c.content_type,
        "taken_at": c.taken_at,
        "caption": c.text,                        # OCR / 转写 / 原文
        "url": c.cos_key or c.thumbnail_key,      # 存储 URL 引用（不含图片二进制）
        "thumbnail_key": c.thumbnail_key,
        "gps_lat": c.gps_lat,
        "gps_lng": c.gps_lng,
        "place": c.place,
        "emotion": c.emotion,
        "sensitive_status": c.sensitive_status,
        "source": c.source,
        "status": c.status,
        "created_at": c.created_at,
        "deleted_at": c.deleted_at,               # 软删 30 天内仍可回溯
    }


def _event_out(e: Event, member_map: dict[str, list[str]]) -> dict:
    return {
        "event_id": str(e.id),
        "level": e.level,
        "title": e.title,
        "start_time": e.start_time,
        "confidence": e.confidence,
        "client_event_id": e.client_event_id,
        "parent_event_id": str(e.parent_event_id) if e.parent_event_id else None,
        "title_source": e.title_source,
        "cover_content_id": str(e.cover_content_id) if e.cover_content_id else None,
        "end_time": e.end_time,
        "place": e.place,
        "emotion": e.emotion,
        "sensitivity": e.sensitivity,
        "status": e.status,
        "generated_by": e.generated_by,
        "created_at": e.created_at,
        "updated_at": e.updated_at,
        "deleted_at": e.deleted_at,
        "item_content_ids": member_map.get(str(e.id), []),   # 事件成员（多对多）
    }


def _correction_out(r: CorrectionLog) -> dict:
    return {
        "content_id": str(r.content_id) if r.content_id else None,
        "old_label": r.old_label,
        "new_label": r.new_label,
        "source": r.source,
        "content_type": r.content_type,
        "confidence": r.confidence,
        "created_at": r.created_at,
    }


def _profile_out(db: Session, user_id: str) -> dict:
    profile = db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    ).scalar_one_or_none()
    sensitive = db.execute(
        select(ProfileSensitive)
        .where(ProfileSensitive.user_id == user_id)
        .order_by(ProfileSensitive.updated_at.desc())
    ).scalars().all()
    return {
        "dimensions": profile.dimensions if profile else {},
        "version": profile.version if profile else 0,
        "cold_start_done": bool(profile.dimensions if profile else {}),
        "updated_at": profile.updated_at if profile else None,
        "sensitive": [
            {
                "id": s.id,
                "topic": s.topic,
                "topic_hash": s.topic_hash,
                "disposition": s.disposition,
                "evidence": s.evidence or [],
                "locked": s.locked,
                "added_at": s.added_at,
                "updated_at": s.updated_at,
            }
            for s in sensitive
        ],
    }


def _hash_user_id(user_id: str) -> str:
    """导出响应中的用户标识：sha256 前缀（不暴露原始 user_id，防 ID 枚举）"""
    return hashlib.sha256(user_id.encode()).hexdigest()[:16]


def build_export_payload(db: Session, user_id: str) -> dict:
    """全量导出负载：contents + events(+成员) + profile(+敏感项) + corrections。

    同步返回（拍板 D1 全量 JSON）；行按 id 键集分块取出并截断到每类上限；
    datetime 保持原值，由 API 层统一 ISO8601 序列化。
    """
    contents, contents_trunc = _collect_capped(db, Content, user_id)
    events, events_trunc = _collect_capped(db, Event, user_id)
    corrections, corr_trunc = _collect_capped(db, CorrectionLog, user_id)
    truncated = contents_trunc or events_trunc or corr_trunc

    member_map: dict[str, list[str]] = {}
    if events:
        event_ids = [str(e.id) for e in events]
        rows = db.execute(
            select(EventItem.event_id, EventItem.content_id).where(
                EventItem.event_id.in_(event_ids)
            )
        ).all()
        for ev_id, cid in rows:
            member_map.setdefault(str(ev_id), []).append(str(cid))

    return {
        "schema_version": "1.0",
        "exported_at": datetime.now(timezone.utc),
        "user_id_hashed": _hash_user_id(user_id),
        "contents": [_content_out(c) for c in contents],
        "events": [_event_out(e, member_map) for e in events],
        "profile": _profile_out(db, user_id),
        "corrections": [_correction_out(r) for r in corrections],
        "truncated": truncated,
    }
