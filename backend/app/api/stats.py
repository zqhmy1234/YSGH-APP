"""统计域端点（R9-7 · Q5 拍板：hero 副标题 API 化）

GET /api/v1/stats/daily-summary —— hero 副标题数据源（前端 onLoad 拉取，
替代 R8 本地 timeline 全量计算）。单聚合查询，口径对齐前端 hero 现状：
  - count：事件经 event_items 关联的 distinct 内容数（=前端 ΣcontentCount 等价，
    全内容类型；无关联内容时退化计数在客户端做，保持 API 纯聚合）
  - latest_event_text / latest_event_time：最新非软删事件的 title / start_time
"""
from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user
from app.db.models import Content, Event, EventItem, User
from app.db.session import get_db
from app.schemas.common import ApiResponse

router = make_router(prefix="/api/v1/stats", tags=["stats"])


@router.get("/daily-summary", response_model=ApiResponse[dict])
def daily_summary(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """hero 副标题三字段（count/latest_event_text/latest_event_time）

    口径（事件域对齐 /events/stats：user_id + deleted_at IS NULL）。
    无事件时返回 count=0 + 两个 None——前端判空清空 hero 文案（不造假）。
    """
    latest = db.execute(
        select(Event.id, Event.title, Event.start_time)
        .where(Event.user_id == user.id, Event.deleted_at.is_(None))
        .order_by(Event.start_time.desc().nullslast(), Event.id.desc())
        .limit(1)
    ).first()
    count_row = db.execute(
        select(func.count(func.distinct(Content.id)))
        .select_from(Event)
        .join(EventItem, EventItem.event_id == Event.id)
        .join(Content, Content.id == EventItem.content_id)
        .where(Event.user_id == user.id, Event.deleted_at.is_(None))
    ).scalar()
    # BA1（迁移 f2a3b4c5d6e7）：用量字节聚合——该用户未删除内容 size_bytes 求和
    # （NULL 不参与 SUM；全空 → None → 归 0，与前端「无数据清零」口径一致）
    total_bytes = db.execute(
        select(func.coalesce(func.sum(Content.size_bytes), 0)).where(
            Content.user_id == user.id, Content.deleted_at.is_(None)
        )
    ).scalar()
    return ApiResponse(
        data={
            "count": int(count_row or 0),
            "latest_event_text": latest.title if latest else None,
            "latest_event_time": (
                latest.start_time.isoformat() if latest and latest.start_time else None
            ),
            "total_bytes": int(total_bytes or 0),
        }
    )
