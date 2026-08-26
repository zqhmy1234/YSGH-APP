"""B4 同步契约（数据同步与离线优先）"""
from pydantic import BaseModel, Field, field_validator

# R6#12（输入校验）：实体 ID 必须为 UUID 格式（防任意串进 UUID 列触发 DB 层 500）
_UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
# value 深层界：嵌套深度 + 容器条目总数双上限（防深层 dict DoS/存储膨胀）
_MAX_VALUE_DEPTH = 6
_MAX_VALUE_ITEMS = 256


class SyncOp(BaseModel):
    """客户端操作（op_id 全局唯一，幂等）"""

    op_id: str = Field(..., min_length=1, max_length=64)
    op_type: str = Field(..., pattern="^(upsert_field|delete)$")
    entity_type: str = Field("content", pattern="^(content|event|profile)$")
    entity_id: str = Field(..., pattern=_UUID_PATTERN, description="实体 ID（uuid）")
    field: str | None = Field(None, description="upsert_field 时必填")
    value: dict | str | float | int | bool | list | None = None
    updated_at: str | None = Field(None, description="客户端修改时间 ISO8601")

    @field_validator("value")
    @classmethod
    def _bound_value(cls, v):
        """R6#12：value 为容器时限制嵌套深度与条目总数（标量直通）"""
        if v is None or isinstance(v, (str, float, int, bool)):
            return v
        count = 0
        stack = [(v, 0)]
        while stack:
            node, depth = stack.pop()
            count += 1
            if count > _MAX_VALUE_ITEMS:
                raise ValueError(f"value 条目数超出上限（{_MAX_VALUE_ITEMS}）")
            if depth > _MAX_VALUE_DEPTH:
                raise ValueError(f"value 嵌套深度超出上限（{_MAX_VALUE_DEPTH}）")
            if isinstance(node, dict):
                for sub in node.values():
                    if isinstance(sub, (dict, list)):
                        stack.append((sub, depth + 1))
            elif isinstance(node, list):
                for sub in node:
                    if isinstance(sub, (dict, list)):
                        stack.append((sub, depth + 1))
        return v


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

    entity_id: str = Field(..., pattern=_UUID_PATTERN, description="实体 ID（uuid）")
    updated_at: str | None = None
    deleted: bool = False


class SyncReconcileRequest(BaseModel):
    items: list[SyncReconcileItem] = Field(..., max_length=5000)


class SyncReconcileResult(BaseModel):
    missing_on_cloud: list[dict]
    missing_on_client: list[dict]
    divergent: list[dict]
    summary: dict
