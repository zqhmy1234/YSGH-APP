"""B4 同步契约（数据同步与离线优先）"""
from pydantic import BaseModel, Field


class SyncOp(BaseModel):
    """客户端操作（op_id 全局唯一，幂等）"""

    op_id: str = Field(..., min_length=1, max_length=64)
    op_type: str = Field(..., pattern="^(upsert_field|delete)$")
    entity_type: str = Field("content", pattern="^(content|event|profile)$")
    entity_id: str = Field(..., description="实体 ID（uuid）")
    field: str | None = Field(None, description="upsert_field 时必填")
    value: dict | str | float | int | bool | list | None = None
    updated_at: str | None = Field(None, description="客户端修改时间 ISO8601")


class SyncPushRequest(BaseModel):
    device_id: str = Field(..., min_length=1, max_length=64)
    ops: list[SyncOp] = Field(..., max_length=500)


class SyncPushResult(BaseModel):
    applied: list[dict]
    conflicts: list[dict]
    rejected: list[dict] = []   # 越权/非法操作被拒（安全修复：实体归属校验）
    server_version: int


class SyncPullResult(BaseModel):
    changes: list[dict]
    cursor: int
    has_more: bool


class SyncReconcileItem(BaseModel):
    """客户端对账条目（本地快照）"""

    entity_id: str = Field(..., description="实体 ID（uuid）")
    updated_at: str | None = None
    deleted: bool = False


class SyncReconcileRequest(BaseModel):
    items: list[SyncReconcileItem] = Field(..., max_length=5000)


class SyncReconcileResult(BaseModel):
    missing_on_cloud: list[dict]
    missing_on_client: list[dict]
    divergent: list[dict]
    summary: dict
