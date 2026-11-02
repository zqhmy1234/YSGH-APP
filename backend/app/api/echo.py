"""回响 API（P2-ECHO · 去年今日）

GET  /api/v1/echo/today        —— 去年今日回响（每天 ≤1 条，敏感排除）
POST /api/v1/echo/{content_id}/dismiss —— 划掉不再出现
"""
from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user
from app.db.models import EchoHistory, User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.echo import EchoDismissOut, EchoTodayOut
from app.services.echo import _fingerprint, dismiss_echo, get_today_echo

router = make_router(prefix="/api/v1/echo", tags=["echo"])


@router.get("/today", response_model=ApiResponse[EchoTodayOut])
def echo_today(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = get_today_echo(db, user.id)
    return ApiResponse(data=result)


@router.post("/{content_id}/dismiss", response_model=ApiResponse[EchoDismissOut])
def echo_dismiss(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """划掉不再出现（R4#15 幂等：同内容已划掉 → 直接返回成功，不重复落 dismiss 记录）

    幂等键 = (user_id, fingerprint)；content_id 经确定性指纹（uuid5）稳定映射，
    重复 dismiss 不再叠加 EchoHistory 行（不重复计数）。
    """
    fp = _fingerprint(content_id)
    already = db.scalar(
        select(func.count())
        .select_from(EchoHistory)
        .where(
            EchoHistory.user_id == user.id,
            EchoHistory.action == "dismiss",
            EchoHistory.fingerprint == fp,
        )
    )
    if not already:
        dismiss_echo(db, user.id, content_id)
    return ApiResponse(data={"dismissed": True, "content_id": content_id})
