"""API 响应统一封装（对齐测试清单 API-007）"""
from typing import Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一响应体：{code, message, data, request_id}"""

    code: str = "OK"
    message: str = "success"
    data: T | None = None
    request_id: str = ""


class Page(BaseModel, Generic[T]):
    """分页响应（对齐测试清单 API-006 分页与游标）"""

    items: list[T]
    cursor: str | None = None    # 游标分页
    has_more: bool = False
