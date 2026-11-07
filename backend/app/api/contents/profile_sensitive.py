"""画像级敏感对话式增删查（B1-6 / B5b FIX-4）——独立 router（prefix /api/v1/profile）

main.py 注册：app.include_router(profile_sensitive_router)。
本模块由原单文件 app/api/contents.py 拆出（重构波 B2），端点逻辑未变。
"""
from fastapi import Depends
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user
from app.core.errors import (
    ERR_PROFILE_SENSITIVE_001,
    ERR_PROFILE_SENSITIVE_002,
    ERR_PROFILE_SENSITIVE_003,
    ApiError,
)
from app.db.models import ProfileSensitive, User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.content import (
    ProfileSensitiveCreate,
    ProfileSensitiveDeleteOut,
    ProfileSensitiveOut,
)

profile_sensitive_router = make_router(prefix="/api/v1/profile", tags=["profile-sensitive"])


def _profile_sensitive_out(row: ProfileSensitive) -> ProfileSensitiveOut:
    return ProfileSensitiveOut(
        id=row.id,
        topic=row.topic,
        disposition=row.disposition,
        evidence=row.evidence or [],
        locked=row.locked,
        added_at=row.added_at,
        updated_at=row.updated_at,
    )


@profile_sensitive_router.post("/sensitive", response_model=ApiResponse[ProfileSensitiveOut])
def profile_sensitive_add(
    req: ProfileSensitiveCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """画像级敏感增/改（B1-6 对话式："别跟我提 X" → 记录话题+处置；幂等 upsert）"""
    from app.services.echo import upsert_profile_sensitive

    try:
        row = upsert_profile_sensitive(
            db, user.id, req.topic, req.disposition, req.evidence, req.locked
        )
    except ValueError as exc:
        raise ApiError(ERR_PROFILE_SENSITIVE_001, str(exc), http=422) from exc
    return ApiResponse(data=_profile_sensitive_out(row))


@profile_sensitive_router.delete("/sensitive", response_model=ApiResponse[ProfileSensitiveDeleteOut])
def profile_sensitive_delete(
    topic: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """画像级敏感删（B1-6）：DELETE /api/v1/profile/sensitive?topic=xxx"""
    from app.services.echo import delete_profile_sensitive

    if not topic.strip():
        raise ApiError(ERR_PROFILE_SENSITIVE_002, "topic 不能为空", http=422)
    deleted = delete_profile_sensitive(db, user.id, topic.strip())
    if not deleted:
        raise ApiError(ERR_PROFILE_SENSITIVE_003, f"话题不存在：{topic}", http=404)
    return ApiResponse(data={"deleted": True, "topic": topic.strip()})


@profile_sensitive_router.get("/sensitive", response_model=ApiResponse[list[ProfileSensitiveOut]])
def profile_sensitive_list(
    disposition: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """画像级敏感查（B1-6）：列出全部话题（可按处置级别过滤），按更新时间倒序"""
    from app.services.echo import list_profile_sensitive

    try:
        rows = list_profile_sensitive(db, user.id, disposition)
    except ValueError as exc:
        raise ApiError(ERR_PROFILE_SENSITIVE_001, str(exc), http=422) from exc
    return ApiResponse(data=[_profile_sensitive_out(r) for r in rows])
