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

import base64
import hashlib
import io
import json
import zipfile
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Content,
    CorrectionLog,
    Event,
    EventItem,
    MemoryEntry,
    Person,
    ProfileSensitive,
    UserProfile,
)

# 每块取数上限（分页防护：单查询不一次全表）
_CHUNK = 500
# 每类导出上限（超出截断并置 truncated=True，防超大库单响应失控）
EXPORT_MAX_PER_TYPE = 2000


def _collect_capped(
    db: Session,
    model,
    user_id: str,
    cap: int | None = None,
    after=None,
    *,
    key_attr: str = "id",
) -> tuple[list, bool, str | None]:
    """按主键键集分页取用户行（增量游标 key > last_key，PG 端比较）。

    ``key_attr`` 默认 ``id``（UUID/BigInt 通用）；canonical 账本的
    ``memory_entries`` 主键叫 ``entry_id``，所以这里必须可配置——写死 ``id``
    会让整份导出在真库上抛 AttributeError（2026-11-24 实锤）。

    cap 缺省取模块常量 EXPORT_MAX_PER_TYPE（调用期求值，便于测试 monkeypatch 收窄）。
    返回 (rows[:cap], truncated, next_key)——取到 cap 后仍尝试再查一块以探测是否超限。
    """
    if cap is None:
        cap = EXPORT_MAX_PER_TYPE
    key = getattr(model, key_attr)
    rows: list = []
    last_key = after
    while len(rows) <= cap:  # 多取一块用于探测截断
        stmt = select(model).where(model.user_id == user_id).order_by(key)
        if last_key is not None:
            stmt = stmt.where(key > last_key)
        batch = db.execute(stmt.limit(_CHUNK)).scalars().all()
        if not batch:
            break
        rows.extend(batch)
        last_key = getattr(batch[-1], key_attr)
    truncated = len(rows) > cap
    next_key = str(getattr(rows[cap - 1], key_attr)) if truncated and cap else None
    return rows[:cap], truncated, next_key


def _last_id(rows: list, key_attr: str = "id") -> str | None:
    return str(getattr(rows[-1], key_attr)) if rows else None


def _content_out(c: Content, storage=None, user_id: str = "") -> dict:
    out = {
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
    if storage is not None and c.content_type in ("photo", "image"):
        out["download_url"] = storage.get_download_url(c.cos_key or "", user_id) if c.cos_key else None
        out["thumbnail_url"] = (
            storage.get_download_url(c.thumbnail_key, user_id) if c.thumbnail_key else None
        )
    return out


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


def encode_cursor(values: dict[str, str]) -> str:
    return base64.urlsafe_b64encode(json.dumps(values, sort_keys=True).encode()).decode()


def decode_cursor(cursor: str | None) -> dict[str, str]:
    if not cursor:
        return {}
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    except Exception as exc:  # noqa: BLE001 —— 非法游标必须 422，不静默从头导出
        raise ValueError("invalid cursor") from exc
    if not isinstance(raw, dict) or not all(isinstance(v, str) for v in raw.values()):
        raise ValueError("invalid cursor")
    return raw


def build_export_payload(
    db: Session,
    user_id: str,
    cursor: str | None = None,
    *,
    include_media_links: bool = False,
) -> dict:
    """全量导出负载：contents + events(+成员) + profile(+敏感项) + corrections + memory。

    同步返回（拍板 D1 全量 JSON）；行按 id 键集分块取出并截断到每类上限；
    `truncated=true` 时 **必须** 带 `next_cursor`，否则客户端无法续拉。
    """
    after = decode_cursor(cursor)
    storage = None
    if include_media_links:
        try:
            from app.services.external.storage import get_storage_backend

            storage = get_storage_backend()
        except Exception:  # noqa: BLE001 —— 链接失败不阻断元数据导出
            storage = None
    contents, contents_trunc, contents_next = _collect_capped(
        db, Content, user_id, after=after.get("contents")
    )
    events, events_trunc, events_next = _collect_capped(
        db, Event, user_id, after=after.get("events")
    )
    corrections, corr_trunc, corr_next = _collect_capped(
        db, CorrectionLog, user_id, after=after.get("corrections")
    )
    memories, memory_trunc, memory_next = _collect_capped(
        db, MemoryEntry, user_id, after=after.get("memory"), key_attr="entry_id"
    )
    persons, person_trunc, person_next = _collect_capped(
        db, Person, user_id, after=after.get("persons")
    )
    truncated = any(
        (contents_trunc, events_trunc, corr_trunc, memory_trunc, person_trunc)
    )
    next_cursor = None
    if truncated:
        # 未截断的类别也带上"最后一页的 id"，保证续拉只前进、不重复。
        next_ids = {
            key: (value or _last_id(rows))
            for key, value, rows in (
                ("contents", contents_next, contents),
                ("events", events_next, events),
                ("corrections", corr_next, corrections),
                ("memory", memory_next, memories),
                ("persons", person_next, persons),
            )
            if value or rows
        }
        next_cursor = encode_cursor(next_ids)

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
        "exported_at": datetime.now(UTC),
        "user_id_hashed": _hash_user_id(user_id),
        "contents": [_content_out(c, storage, user_id) for c in contents],
        "events": [_event_out(e, member_map) for e in events],
        "profile": _profile_out(db, user_id),
        "corrections": [_correction_out(r) for r in corrections],
        "memory": [
            {
                "entry_id": m.entry_id,
                "kind": m.kind,
                "text": m.text,
                "summary": m.summary,
                "valid_from": m.valid_from,
                "valid_to": m.valid_to,
                "evidence": m.evidence,
                "confidence": float(m.confidence),
                "sensitivity": m.sensitivity,
            }
            for m in memories
        ],
        "persons": [
            {
                "person_id": str(p.id),
                "display_name": p.display_name,
                "relation": p.relation,
                "epistemic": p.epistemic,
                "created_at": p.created_at,
                "deleted_at": p.deleted_at,
            }
            for p in persons
        ],
        "truncated": truncated,
        "next_cursor": next_cursor,
    }


EXPORT_ZIP_MAX_BYTES = 50 * 1024 * 1024


def build_export_zip(
    payload: dict,
    *,
    storage=None,
    max_bytes: int = EXPORT_ZIP_MAX_BYTES,
) -> tuple[bytes, bool]:
    """B5-04: text/voice/metadata into a ZIP; photos stay as signed COS links.

    Photos are intentionally *not* embedded (the export contract gives links so the
    client can fetch originals directly). Voice bytes and transcripts are included
    because the user asked for a portable archive of their own content. If the ZIP
    would exceed ``max_bytes``, the entry is skipped and ``truncated=True`` is
    returned rather than silently producing a half-written archive.
    """
    if storage is None:
        from app.services.external.storage import get_storage_backend

        storage = get_storage_backend()
    buf = io.BytesIO()
    truncated = bool(payload.get("truncated"))
    warnings: list[str] = []
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "metadata.json",
            json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        )
        for item in payload.get("contents") or []:
            cid = str(item.get("content_id") or "")
            ctype = item.get("content_type") or ""
            text = (item.get("caption") or "").strip()
            if ctype == "text" and text:
                zf.writestr(f"texts/{cid}.txt", text)
            if ctype == "voice":
                if text:
                    zf.writestr(f"transcripts/{cid}.txt", text)
                key = item.get("url") or ""
                if not key:
                    warnings.append(f"voice_missing_key:{cid}")
                    continue
                try:
                    data = storage.get_object(key)
                except Exception as exc:  # noqa: BLE001 —— 单个对象失败不拖垮整个 ZIP
                    warnings.append(f"voice_fetch_failed:{cid}:{type(exc).__name__}")
                    continue
                suffix = "." + key.rsplit(".", 1)[-1] if "." in key.rsplit("/", 1)[-1] else ".wav"
                if buf.tell() + len(data) > max_bytes:
                    truncated = True
                    warnings.append(f"zip_size_limit:{cid}")
                    break
                zf.writestr(f"audio/{cid}{suffix}", data)
        if warnings:
            payload["zip_warnings"] = warnings
    return buf.getvalue(), truncated
