"""B4 数据同步 API（云端主库 · 字段级 LWW · 游标幂等）

POST /api/v1/sync/push —— 客户端提交操作批次（幂等，返回权威版本 + 冲突提示）
GET  /api/v1/sync/pull —— 增量拉取（since 游标，返回变更日志 + 新游标）
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.sync import (
    SyncPullResult,
    SyncPushRequest,
    SyncPushResult,
    SyncReconcileRequest,
    SyncReconcileResult,
)
from app.services.reconcile import reconcile_snapshot
from app.services.sync import pull_changes, push_ops_safe

router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


@router.post("/push", response_model=ApiResponse[SyncPushResult])
def sync_push(
    req: SyncPushRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = push_ops_safe(db, user.id, req.device_id, [op.model_dump() for op in req.ops])
    return ApiResponse(data=SyncPushResult(**result))


@router.get("/pull", response_model=ApiResponse[SyncPullResult])
def sync_pull(
    device_id: str,
    since: int = 0,
    limit: int = 200,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = pull_changes(db, user.id, device_id, since=since, limit=limit)
    return ApiResponse(data=SyncPullResult(**result))


@router.post("/reconcile", response_model=ApiResponse[SyncReconcileResult])
def sync_reconcile(
    req: SyncReconcileRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """端云对账（S5-04）：客户端本地快照 vs 云端权威 → 差异报告"""
    result = reconcile_snapshot(db, user.id, [item.model_dump() for item in req.items])
    return ApiResponse(data=SyncReconcileResult(**result))
