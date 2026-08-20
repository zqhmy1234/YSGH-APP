"""回响 API（P2-ECHO · 去年今日）

GET  /api/v1/echo/today        —— 去年今日回响（每天 ≤1 条，敏感排除）
POST /api/v1/echo/{content_id}/dismiss —— 划掉不再出现
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.services.echo import dismiss_echo, get_today_echo

router = APIRouter(prefix="/api/v1/echo", tags=["echo"])


@router.get("/today", response_model=ApiResponse)
def echo_today(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = get_today_echo(db, user.id)
    return ApiResponse(data=result)


@router.post("/{content_id}/dismiss", response_model=ApiResponse)
def echo_dismiss(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    dismiss_echo(db, user.id, content_id)
    return ApiResponse(data={"dismissed": True, "content_id": content_id})
