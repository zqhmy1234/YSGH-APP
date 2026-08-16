"""事件路由：四层事件模型（B3）+ 时间轴（F8）+ 用户手动操作（B3-5）"""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.event import (
    EventConfirmRequest,
    EventMergeRequest,
    EventOut,
    EventSplitRequest,
)

router = APIRouter(prefix="/api/v1/events", tags=["events"])

_MOCK_EVENTS: list[dict] = []


@router.get("/timeline", response_model=ApiResponse[list[EventOut]])
def timeline(level: int | None = None, db: Session = Depends(get_db)):
    """时间轴（F8）：L1 日卡片 + L2 主题事件 + L3 主题流；按 start_time 排序"""
    items = [EventOut(**e) for e in _MOCK_EVENTS if level is None or e["level"] == level]
    items.sort(key=lambda e: e.start_time or datetime(1970, 1, 1, tzinfo=timezone.utc), reverse=True)
    return ApiResponse(data=items)


@router.post("/merge", response_model=ApiResponse[EventOut])
def merge_events(req: EventMergeRequest, db: Session = Depends(get_db)):
    """用户合并（B3-5：存合并规则，算法永不覆盖用户决定，AGG-013）"""
    raise ApiError("EVENT_099", "事件服务未接入（M2 实现）", http=501)


@router.post("/split", response_model=ApiResponse[EventOut])
def split_event(req: EventSplitRequest, db: Session = Depends(get_db)):
    raise ApiError("EVENT_099", "事件服务未接入（M2 实现）", http=501)


@router.post("/confirm", response_model=ApiResponse[EventOut])
def confirm_event(req: EventConfirmRequest, db: Session = Depends(get_db)):
    """用户确认（置信度<0.7 转正；用户背书后不再被算法改动）"""
    raise ApiError("EVENT_099", "事件服务未接入（M2 实现）", http=501)
