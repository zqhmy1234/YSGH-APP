"""W2-2 收藏（2026-09-05）：单条收藏端点 + 收藏列表

设计要点（拆包前注释原文保留）：
- 收藏持久化走 contents.extra JSONB 键 favorite_at（零迁移；pipeline.set_extra 同款
  「dict 重建再赋值」写法防 JSONB in-place mutation 不触发 UPDATE）
- favorite×3 挂在 contents 主 router（/api/v1/contents/{content_id}/favorite，共享
  serializers.router）；收藏列表走 /api/v1/favorites 独立 prefix router
  （与 /api/v1/contents/{id} 路径段数不同零冲突）

本模块由原单文件 app/api/contents.py 拆出（重构波 B2），端点逻辑未变。
"""
import logging
from datetime import datetime, timezone

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.contents.serializers import _to_out, router
from app.api.deps import get_current_user, load_alive_content
from app.db.models import Content, User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.content import ContentOut, FavoriteOut
from app.services.external.media_url import content_urls
from app.services.sync_common import parse_ts

favorites_router = make_router(prefix="/api/v1/favorites", tags=["favorites"])


@router.post("/{content_id}/favorite", response_model=ApiResponse[FavoriteOut])
def favorite_add(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """收藏内容（W2-2）：extra.favorite_at 落 ISO 时间戳；重复收藏幂等返回既有态"""
    row = load_alive_content(db, user.id, content_id)
    extra = dict(row.extra or {})
    existing = extra.get("favorite_at")
    if existing is None:
        extra["favorite_at"] = datetime.now(timezone.utc).isoformat()
        row.extra = extra
        db.commit()
        existing = row.extra["favorite_at"]
    fav_dt = parse_ts(str(existing)) if existing else None
    return ApiResponse(data=FavoriteOut(content_id=row.id, favorite=True, favorite_at=fav_dt))


@router.delete("/{content_id}/favorite", response_model=ApiResponse[FavoriteOut])
def favorite_remove(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """取消收藏（W2-2）：extra.favorite_at 摘除；未收藏幂等返回 favorite=false"""
    row = load_alive_content(db, user.id, content_id)
    extra = dict(row.extra or {})
    had = extra.pop("favorite_at", None)
    row.extra = extra
    if had is not None:
        db.commit()
    return ApiResponse(data=FavoriteOut(content_id=row.id, favorite=False, favorite_at=None))


@router.get("/{content_id}/favorite", response_model=ApiResponse[FavoriteOut])
def favorite_get(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """查询单条收藏态（W2-2：detail 页星标初始化用）"""
    row = load_alive_content(db, user.id, content_id)
    fav_raw = (row.extra or {}).get("favorite_at")
    fav_dt = parse_ts(str(fav_raw)) if fav_raw else None
    return ApiResponse(data=FavoriteOut(content_id=row.id, favorite=fav_dt is not None, favorite_at=fav_dt))


@favorites_router.get("", response_model=ApiResponse[list[ContentOut]])
def favorites_list(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """收藏列表（W2-2）：extra.favorite_at 非空且未删除，按收藏时间倒序 + 缩略图签票"""
    rows = (
        db.execute(
            select(Content)
            .where(
                Content.user_id == user.id,
                Content.deleted_at.is_(None),
                Content.extra["favorite_at"].astext.isnot(None),
            )
            .order_by(Content.extra["favorite_at"].astext.desc())
        )
        .scalars()
        .all()
    )
    out: list[ContentOut] = []
    for c in rows:
        item = _to_out(c)
        if c.thumbnail_key:
            try:
                _orig, thumb = content_urls(None, c.thumbnail_key, str(user.id))
                item.thumbnail_url = thumb
            except Exception:  # 签票失败降级无图不阻塞主链路（recall.py 同口径）
                logging.getLogger(__name__).warning(
                    "收藏缩略图签票失败 content_id=%s", c.id, exc_info=True
                )
        out.append(item)
    return ApiResponse(data=out)
