"""事件路由：四层事件模型（B3）+ 时间轴（F8）+ 用户手动操作（B3-5）"""

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user, uuid4_str
from app.core.errors import ERR_EVENT_004, ERR_EVENT_006, ERR_EVENT_007, ApiError
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.event import (
    EventConfirmRequest,
    EventCoverRequest,
    EventItemOut,
    EventMergeRequest,
    EventOut,
    EventPhotoOut,
    EventSplitRequest,
    EventSyncRequest,
    EventSyncResult,
    VoiceInfo,
)
from app.services.errors import ConflictError, NotFoundError, ValidationError

router = make_router(prefix="/api/v1/events", tags=["events"])

# 每个事件随 timeline 下发的成员照片上限（卡片照片条渲染所需；原图按需另取）。
# 取 6：卡片横向照片条设计容量 3-6 张，再多是浪费带宽且拖慢首屏。
PHOTOS_PER_EVENT = 6

# 每个事件随 timeline 下发的语音成员上限（W0-2 就地播放：卡片语音卡单条，取首条足够）。
VOICES_PER_EVENT = 1


@router.get("/stats", response_model=ApiResponse[dict])
def event_stats(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """派生统计（性能卡 PG：单聚合查询，替代前端 fetchTimeline(null) 全量拉取派生）

    口径（事件域对齐 timeline：user_id + deleted_at IS NULL，services/events/timeline.py）：
    - event_count：非软删事件总数（含各 level/status）
    - day_count：非空 start_time 按天去重天数（对齐前端 dayKey(start_time)）
    - photo_count：事件经 event_items 关联的 distinct 照片数（content_type=photo，不截断）
    - unread_count：messages status=unread 条数（标量子查询并入本查询）
    """
    from sqlalchemy import func

    from app.db.models import Content, Event, EventItem, Message

    unread_sq = (
        select(func.count())
        .select_from(Message)
        .where(Message.user_id == user.id, Message.status == "unread")
    ).scalar_subquery()
    row = db.execute(
        select(
            func.count(func.distinct(Event.id)),
            func.count(func.distinct(func.date(Event.start_time))),
            func.count(func.distinct(Content.id)),
            unread_sq,
        )
        .select_from(Event)
        .outerjoin(EventItem, EventItem.event_id == Event.id)
        .outerjoin(
            Content,
            (Content.id == EventItem.content_id) & (Content.content_type == "photo"),
        )
        .where(Event.user_id == user.id, Event.deleted_at.is_(None))
    ).one()
    return ApiResponse(
        data={
            "event_count": int(row[0]),
            "day_count": int(row[1]),
            "photo_count": int(row[2]),
            "unread_count": int(row[3]),
        }
    )


@router.get("/timeline", response_model=ApiResponse[list[EventOut]])
def timeline(
    level: int | None = None,
    status: str | None = None,
    pending: bool | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """时间轴（F8）：L1 日卡片 + L2 主题事件 + L3 主题流；按 start_time 排序

    Wave2-AgentD：L2 待确认区筛选——
      pending=true → level≥2 且 status=draft 且 confidence<0.7（B3-5 <0.7 进待确认）
      status=draft / level=2 组合亦可（客户端自行组合）
    L3 事件附加 lifecycle（活跃 30 天→静默→归档，读取时派生）。
    安全：需登录。
    """
    from app.services.event_aggregation.pipeline import l3_lifecycle
    from app.services.events import get_event_last_activity, get_timeline

    events = get_timeline(db, str(user.id), level=level, status=status, pending=bool(pending))
    event_ids = [e.id for e in events]
    # 审查修复(P1-11)：一次 GROUP BY 批量取计数，消除 N+1（原逐事件 count 查询）
    counts = _batch_counts(db, event_ids)
    # photo_ids 保留兼容：客户端 photoPathOf 兜底通路（thumbnails/{cid} + downloadFile header）；
    # 主通路为下方 photos[].thumbnail_url 票据直发（Valet Key）
    photo_ids = _batch_photo_ids(db, event_ids)
    # L3 生命周期：批量取最近活动 → 派生状态（读取时计算，MVP 不落库）
    last_act = get_event_last_activity(db, str(user.id), event_ids)
    lifecycles = {
        str(e.id): l3_lifecycle(e.start_time, last_act.get(str(e.id)))
        for e in events
        if e.level >= 3
    }
    # 图片下发（2026-08-31）：成员照片 + 封面 URL 一次批量组装。
    # 此前 EventOut 不带任何成员信息 → 客户端冷启动拿不到照片 → 卡片照片条永远空。
    # 两个批量查询覆盖全部事件，不随事件数线性增长（防 N+1）。
    uid = str(user.id)
    photos_by_event = _batch_event_photos(db, event_ids, uid)
    voices_by_event = _batch_event_voices(db, event_ids, uid)
    cover_urls = _batch_cover_urls(db, [e.cover_content_id for e in events], uid)
    return ApiResponse(
        data=[
            _to_out(
                e,
                counts,
                lifecycles.get(str(e.id)),
                photos=photos_by_event.get(str(e.id)),
                voice=voices_by_event.get(str(e.id)),
                cover_url=cover_urls.get(str(e.cover_content_id)) if e.cover_content_id else None,
                photo_ids=photo_ids.get(str(e.id)),
            )
            for e in events
        ]
    )


def _attach_thumbnail_urls(db: Session, items: list[dict], user_id: str) -> None:
    """就地给成员明细补 thumbnail_url（一次 IN 查询）

    拆分选片 UI 要能看到缩略图才能勾选，此前该端点无任何图片字段。
    只给照片成员生成（文字/语音无 thumbnail_key → content_urls 返回 None）。
    """
    from app.db.models import Content
    from app.services.external.media_url import content_urls

    ids = [i.get("content_id") for i in items if i.get("content_id")]
    if not ids:
        return
    rows = db.execute(
        select(Content.id, Content.thumbnail_key).where(
            Content.id.in_(ids),
            Content.user_id == user_id,
            Content.deleted_at.is_(None),
        )
    ).all()
    urls: dict[str, str] = {}
    for r in rows:
        _orig, thumb = content_urls(None, r.thumbnail_key, user_id)
        if thumb:
            urls[str(r.id)] = thumb
    for item in items:
        item["thumbnail_url"] = urls.get(item.get("content_id"))


def _batch_event_photos(db: Session, event_ids: list, user_id: str) -> dict[str, list]:
    """批量取事件成员照片（一次窗口函数查询，防 N+1）

    每事件最多 PHOTOS_PER_EVENT 张（ROW_NUMBER 分区截断，不把 500 张全捞出来）。
    排序 taken_at 升序（PG 的 ASC 默认 NULLS LAST），与卡片照片条展示顺序一致。
    归属：Content.user_id 限定当前用户（防跨用户读）。
    """
    from sqlalchemy import func

    from app.db.models import Content, EventItem

    if not event_ids:
        return {}
    rn = func.row_number().over(
        partition_by=EventItem.event_id,
        order_by=[Content.taken_at.asc(), Content.created_at.asc()],
    ).label("rn")
    subq = (
        select(
            EventItem.event_id.label("event_id"),
            Content.id.label("content_id"),
            Content.cos_key,
            Content.thumbnail_key,
            Content.taken_at,
            Content.place,
            Content.text,
            rn,
        )
        .join(Content, Content.id == EventItem.content_id)
        .where(
            EventItem.event_id.in_(event_ids),
            Content.content_type == "photo",
            Content.deleted_at.is_(None),
            Content.user_id == user_id,
        )
        .subquery()
    )
    rows = db.execute(select(subq).where(subq.c.rn <= PHOTOS_PER_EVENT)).all()
    from app.services.external.media_url import content_urls

    out: dict[str, list] = {}
    for r in rows:
        original_url, thumbnail_url = content_urls(r.cos_key, r.thumbnail_key, user_id)
        out.setdefault(str(r.event_id), []).append(
            EventPhotoOut(
                content_id=str(r.content_id),
                thumbnail_url=thumbnail_url,
                original_url=original_url,
                taken_at=r.taken_at,
                place=r.place,
                title=r.text,
            )
        )
    return out


def _batch_event_voices(db: Session, event_ids: list, user_id: str) -> dict[str, VoiceInfo | None]:
    """批量取事件成员语音（W0-2 · 2026-09-02：timeline 语音卡就地播放）

    每事件最多 VOICES_PER_EVENT 条（ROW_NUMBER 分区截断）；归属同 _batch_event_photos。
    只下发 content_id + title（语音转写首句），不带 url——播放走鉴权端点
    GET /api/v1/media/audio/{content_id}（token 只能走 header，客户端先落临时文件）。
    duration 暂无字段，客户端播放时由 InnerAudioContext.duration 补齐。
    """
    from sqlalchemy import func

    from app.db.models import Content, EventItem

    if not event_ids:
        return {}
    rn = func.row_number().over(
        partition_by=EventItem.event_id,
        order_by=[Content.taken_at.asc(), Content.created_at.asc()],
    ).label("rn")
    subq = (
        select(
            EventItem.event_id.label("event_id"),
            Content.id.label("content_id"),
            Content.text,
            rn,
        )
        .join(Content, Content.id == EventItem.content_id)
        .where(
            EventItem.event_id.in_(event_ids),
            Content.content_type == "voice",
            Content.deleted_at.is_(None),
            Content.user_id == user_id,
        )
        .subquery()
    )
    rows = db.execute(select(subq).where(subq.c.rn <= VOICES_PER_EVENT)).all()
    out: dict[str, VoiceInfo | None] = {}
    for r in rows:
        title = (r.text or "").strip()
        if title == "":
            title = "语音记忆"
        out[str(r.event_id)] = VoiceInfo(
            content_id=str(r.content_id),
            title=title[:40],
        )
    return out


def _batch_cover_urls(db: Session, cover_ids: list, user_id: str) -> dict[str, str]:
    """批量取封面缩略图 URL（一次 IN 查询；封面可能不在成员照片的前 N 张内）

    Returns:
        {content_id: thumbnail_url}
    """
    from app.db.models import Content

    ids = [c for c in cover_ids if c]
    if not ids:
        return {}
    rows = db.execute(
        select(Content.id, Content.cos_key, Content.thumbnail_key).where(
            Content.id.in_(ids),
            Content.user_id == user_id,
            Content.deleted_at.is_(None),
        )
    ).all()
    from app.services.external.media_url import content_urls

    out: dict[str, str] = {}
    for r in rows:
        _orig, thumb = content_urls(r.cos_key, r.thumbnail_key, user_id)
        if thumb:
            out[str(r.id)] = thumb
    return out


def _batch_counts(db: Session, event_ids: list[str]) -> dict[str, dict]:
    """批量计数：{event_id: {content_count, photo_count}}（一次 GROUP BY 查询）"""
    from sqlalchemy import func

    from app.db.models import Content, EventItem

    if not event_ids:
        return {}
    rows = db.execute(
        select(
            EventItem.event_id,
            func.count().label("total"),
            func.count().filter(Content.content_type == "photo").label("photos"),
        )
        .join(Content, Content.id == EventItem.content_id)
        .where(EventItem.event_id.in_(event_ids))
        .group_by(EventItem.event_id)
    ).all()
    return {str(r.event_id): {"content_count": int(r.total), "photo_count": int(r.photos)} for r in rows}


def _batch_photo_ids(db: Session, event_ids: list[str], per_event: int = 4) -> dict[str, list[str]]:
    """批量取每事件成员照片 content_id（taken_at 序，截前 per_event 张；一次查询防 N+1）

    兼容通路：客户端凭 cid 走 /api/v1/thumbnails/{cid}（downloadFile+header）；
    主通路为 photos[]/thumbnail_url 票据直发（Valet Key），两路并存互为兜底。
    """
    from app.db.models import Content, EventItem

    if not event_ids:
        return {}
    rows = db.execute(
        select(EventItem.event_id, Content.id)
        .join(Content, Content.id == EventItem.content_id)
        .where(EventItem.event_id.in_(event_ids), Content.content_type == "photo")
        .order_by(Content.taken_at)
    ).all()
    out: dict[str, list[str]] = {}
    for eid, cid in rows:
        lst = out.setdefault(str(eid), [])
        if len(lst) < per_event:
            lst.append(str(cid))
    return out


def _to_out(
    e,
    counts: dict | None = None,
    lifecycle: dict | None = None,
    photos: list | None = None,
    voice: VoiceInfo | None = None,
    cover_url: str | None = None,
    photo_ids: list[str] | None = None,
) -> EventOut:
    """Event ORM → EventOut（计数预取，审查 P1-11 修复 N+1；L3 附生命周期）

    photos/voice/cover_url 由 timeline 批量传入；merge/split/confirm/cover 等单事件
    操作不传（返回空），客户端需要照片时重新拉 timeline 即可。
    photo_ids 保留兼容（客户端兜底通路）。
    """
    counts = counts or {}
    c = counts.get(str(e.id), {"content_count": 0, "photo_count": 0})
    return EventOut(
        id=str(e.id),
        level=e.level,
        title=e.title,
        title_source=e.title_source,
        cover_content_id=str(e.cover_content_id) if e.cover_content_id else None,
        start_time=e.start_time,
        end_time=e.end_time,
        place=e.place,
        emotion=e.emotion,
        sensitivity=e.sensitivity,
        confidence=e.confidence,
        status=e.status,
        generated_by=e.generated_by,
        content_count=c["content_count"],
        photo_count=c["photo_count"],
        photo_ids=photo_ids or [],
        lifecycle=lifecycle,
        cover_url=cover_url,
        photos=photos or [],
        voice=voice,
    )


@router.post("/sync", response_model=ApiResponse[EventSyncResult])
def sync_events(
    req: EventSyncRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """端侧 L1 事件批量提交（S-SY-1 · B3-6 端侧 L0/L1 真值）

    client_event_id 幂等（网络重试只落一次）；照片归属校验（越权拒绝）；
    落库后云侧只跑 L2/L3 候选（caption/CI 打标保留 _process_photo）。
    变更写入 offline_queue → 其他端增量拉取可见（M4 端间同步一致）。
    """
    from app.services.events import sync_client_events_safe

    result = sync_client_events_safe(
        db,
        str(user.id),
        req.device_id,
        [e.model_dump() for e in req.events],
    )
    return ApiResponse(data=EventSyncResult(**result))


@router.get("/{event_id}/items", response_model=ApiResponse[list[EventItemOut]])
def event_items(
    event_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """事件成员明细（2026-08-25 · split UI 前置）

    返回事件内内容列表（照片/文字/语音，title 可直接展示），
    客户端据此做选片拆分；归属校验（他人事件 404）。
    """
    from app.services.events import get_event_items as _items

    try:
        event_id = uuid4_str(event_id)
        items = _items(db, str(user.id), event_id)
        _attach_thumbnail_urls(db, items, str(user.id))
    except NotFoundError as exc:
        raise ApiError(ERR_EVENT_004, exc.message, http=404) from exc
    except ConflictError as exc:
        raise ApiError(ERR_EVENT_006, exc.message, http=409) from exc
    except ValidationError as exc:
        raise ApiError(ERR_EVENT_007, exc.message, http=422) from exc
    return ApiResponse(data=items)


@router.post("/merge", response_model=ApiResponse[EventOut])
def merge_events(req: EventMergeRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """用户合并（B3-5：存合并规则，算法永不覆盖用户决定，AGG-013）

    2026-08-20：手动操作已接线；source 内容并入 target，source 软删，target 置 confirmed。
    """
    from app.services.events import merge_events as _merge

    try:
        target_id = uuid4_str(req.target_event_id)
        source_ids = [uuid4_str(s) for s in req.source_event_ids]
        ev = _merge(db, str(user.id), target_id, source_ids)
    except NotFoundError as exc:
        raise ApiError(ERR_EVENT_004, exc.message, http=404) from exc
    except ConflictError as exc:
        raise ApiError(ERR_EVENT_006, exc.message, http=409) from exc
    except ValidationError as exc:
        raise ApiError(ERR_EVENT_007, exc.message, http=422) from exc
    return ApiResponse(data=_to_out(ev, _batch_counts(db, [ev.id])))


@router.post("/split", response_model=ApiResponse[EventOut])
def split_event(req: EventSplitRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """用户拆分（B3-5）：拆出内容建独立事件；拆出的新事件置 confirmed"""
    from app.services.events import split_event as _split

    try:
        event_id = uuid4_str(req.event_id)
        ev = _split(db, str(user.id), event_id, req.content_ids)
    except NotFoundError as exc:
        raise ApiError(ERR_EVENT_004, exc.message, http=404) from exc
    except ConflictError as exc:
        raise ApiError(ERR_EVENT_006, exc.message, http=409) from exc
    except ValidationError as exc:
        raise ApiError(ERR_EVENT_007, exc.message, http=422) from exc
    return ApiResponse(data=_to_out(ev, _batch_counts(db, [ev.id])))


@router.post("/confirm", response_model=ApiResponse[EventOut])
def confirm_event(req: EventConfirmRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """用户确认（置信度<0.7 转正；用户背书后不再被算法改动）"""
    from app.services.events import confirm_event as _confirm

    try:
        event_id = uuid4_str(req.event_id)
        ev = _confirm(db, str(user.id), event_id, title=req.title)
    except NotFoundError as exc:
        raise ApiError(ERR_EVENT_004, exc.message, http=404) from exc
    except ConflictError as exc:
        raise ApiError(ERR_EVENT_006, exc.message, http=409) from exc
    except ValidationError as exc:
        raise ApiError(ERR_EVENT_007, exc.message, http=422) from exc
    return ApiResponse(data=_to_out(ev, _batch_counts(db, [ev.id])))


@router.put("/{event_id}/cover", response_model=ApiResponse[EventOut])
def set_cover(
    event_id: str,
    req: EventCoverRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """用户手动换封面（B3-4：人脸优先+质量分自动选，用户可覆盖；cover 必须是事件成员）"""
    from app.services.events import set_event_cover as _set_cover

    try:
        event_id = uuid4_str(event_id)
        ev = _set_cover(db, str(user.id), event_id, req.cover_content_id)
    except NotFoundError as exc:
        raise ApiError(ERR_EVENT_004, exc.message, http=404) from exc
    except ConflictError as exc:
        raise ApiError(ERR_EVENT_006, exc.message, http=409) from exc
    except ValidationError as exc:
        raise ApiError(ERR_EVENT_007, exc.message, http=422) from exc
    return ApiResponse(data=_to_out(ev, _batch_counts(db, [ev.id])))
