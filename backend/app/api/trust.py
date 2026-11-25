"""B6 信任域 API（用途授权 / 保险箱 / 数据活动审计）

GET  /api/v1/trust/consents          —— 用途授权清单（5 项，含未表态）
PUT  /api/v1/trust/consents          —— 授予或撤回一个用途
GET  /api/v1/trust/vault             —— 我锁了哪些内容
POST /api/v1/trust/vault/lock        —— 锁一条内容
POST /api/v1/trust/vault/unlock      —— 解锁（验服务端令牌）
GET  /api/v1/trust/vault/{content_id}/token —— 取解锁令牌（本人当场）
GET  /api/v1/trust/audit             —— 我的数据活动记录
"""
from __future__ import annotations

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user
from app.core.errors import ERR_ECHO_001, ApiError
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.trust import (
    AuditListOut,
    ConsentListOut,
    ConsentSetIn,
    VaultItemOut,
    VaultLockIn,
    VaultTokenOut,
    VaultUnlockIn,
)
from app.services.audit import list_audit
from app.services.consent import list_consents, set_consent
from app.services.vault import (
    list_vault,
    lock_content,
    unlock_content,
    unlock_token_for,
)

router = make_router(prefix="/api/v1/trust", tags=["trust"])


def _iso(dt):
    return dt.isoformat() if dt else None


@router.get("/consents", response_model=ApiResponse[ConsentListOut])
def consents_get(db: Session = Depends(get_db), user=Depends(get_current_user)):
    items = list_consents(db, str(user.id))
    return ApiResponse(data=ConsentListOut(
        items=[{**i, "updated_at": _iso(i["updated_at"])} for i in items]
    ))


@router.put("/consents", response_model=ApiResponse[dict])
def consents_put(
    payload: ConsentSetIn,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    try:
        return ApiResponse(data=set_consent(db, str(user.id), payload.scope, payload.granted))
    except ValueError as exc:
        raise ApiError(ERR_ECHO_001, str(exc), http=422) from exc


@router.get("/vault", response_model=ApiResponse[list[VaultItemOut]])
def vault_list(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return ApiResponse(data=list_vault(db, str(user.id)))


@router.post("/vault/lock", response_model=ApiResponse[dict])
def vault_lock(
    payload: VaultLockIn,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    # 内容归属/存在性错误由 deps.load_alive_content 抛 ApiError，原样透传
    return ApiResponse(data=lock_content(
        db, str(user.id), payload.content_id, note=payload.note
    ))


@router.post("/vault/unlock", response_model=ApiResponse[dict])
def vault_unlock(
    payload: VaultUnlockIn,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    result = unlock_content(db, str(user.id), payload.content_id, payload.key)
    if not result["ok"]:
        raise ApiError(ERR_ECHO_001, "解锁失败：令牌错误或内容未锁定", http=400)
    return ApiResponse(data=result)


@router.get("/vault/{content_id}/token", response_model=ApiResponse[VaultTokenOut])
def vault_token(
    content_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    return ApiResponse(data=VaultTokenOut(
        content_id=content_id, key=unlock_token_for(db, str(user.id), content_id)
    ))


@router.get("/audit", response_model=ApiResponse[AuditListOut])
def audit_list(
    limit: int = Query(50, ge=1, le=200),
    action: str | None = None,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    try:
        items = list_audit(db, str(user.id), limit=limit, action=action)
    except ValueError as exc:
        raise ApiError(ERR_ECHO_001, str(exc), http=422) from exc
    return ApiResponse(data=AuditListOut(
        items=[{**i, "created_at": _iso(i["created_at"])} for i in items]
    ))
