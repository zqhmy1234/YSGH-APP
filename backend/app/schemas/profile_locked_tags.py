"""锁定标签 API 契约（B1:139「用户锁定，LLM 不再自动写回」）。

GET/PUT 同一形状：`{"tags": string[]}`。PUT 为**整体覆盖**（与客户端"完成即全量落盘"
口径一致）——增量语义无法表达"取消固定"。
"""
from pydantic import BaseModel, Field


class ProfileLockedTagsIn(BaseModel):
    """PUT 请求体：整体覆盖的锁定集合（可为空数组 = 全部解锁）。"""

    tags: list[str] = Field(default_factory=list)


class ProfileLockedTagsOut(BaseModel):
    """读/写返回：服务端规整后的最新集合（去空白、去空串、去重、保序）。"""

    tags: list[str] = Field(default_factory=list)
