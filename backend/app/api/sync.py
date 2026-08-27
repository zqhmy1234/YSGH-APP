"""B4 数据同步 API（云端主库 · 字段级 LWW · 游标幂等）

POST /api/v1/sync/push —— 客户端提交操作批次（幂等，返回权威版本 + 冲突提示）
GET  /api/v1/sync/pull —— 增量拉取（since 游标，返回变更日志 + 新游标）

G2/R4#7 加固（2026-08-27）：sync_pull limit 上限校验——超上限拒绝并返回
明确错误码 SYNC_001(422)，防止恶意超大分页打满 DB 查询/内存（纵深）。
"""
from fastapi import Depends
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user
from app.core.errors import ERR_SYNC_001, ApiError
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

router = make_router(prefix="/api/v1/sync", tags=["sync"])

# 单次增量拉取上限（G2/R4#7：防超大分页；客户端按 has_more 分页续拉）
MAX_PULL_LIMIT = 500


def _check_pull_limit(limit: int) -> None:
    """limit 校验：<1 或 >MAX_PULL_LIMIT → 422 SYNC_001（明确错误码，不静默截断）"""
    if limit < 1:
        raise ApiError(ERR_SYNC_001, "limit 必须 ≥ 1", http=422)
    if limit > MAX_PULL_LIMIT:
        raise ApiError(ERR_SYNC_001, f"limit 超过上限 {MAX_PULL_LIMIT}（请按 has_more 分页拉取）", http=422)


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
    _check_pull_limit(limit)
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
