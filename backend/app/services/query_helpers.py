"""查询条件公共片段。

结构棘轮 `soft_delete_filter` 要求未删除条件不得在各查询点反复手抄；
这里提供模型无关的唯一实现。
"""
from __future__ import annotations


def alive_clause(model):
    """返回 `<model>.deleted_at IS NULL` SQLAlchemy 条件。"""
    return model.deleted_at.is_(None)
