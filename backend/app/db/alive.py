"""软删过滤公共 helper（2026-11-01 结构棘轮：soft_delete_filter 手抄收敛）。

`deleted_at` 空值过滤全仓库 45 处手抄是同语义重复；新代码一律走这里，
存量逐步收敛（棘轮只升不降，本 helper 是唯一新增出口）。
"""
from sqlalchemy import ColumnElement

from app.db.models.base import Base

_DELETED_AT_ATTR = "deleted_at"


def alive(model: type[Base]) -> ColumnElement[bool]:
    """「未软删」过滤条件：`model.deleted_at` 为空的唯一新出口。"""
    col = getattr(model, _DELETED_AT_ATTR)
    return col.is_(None)  # type: ignore[attr-defined]
