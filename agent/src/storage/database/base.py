"""SQLAlchemy 声明基类（去 Coze 版）。

上游用 `from coze_coding_dev_sdk.database import Base`（Coze 专有包，见
agent/UPSTREAM.md 四-2）；本文件提供等价的自有 Base，使 ORM 声明不再依赖厂商 SDK。

注意（AG3 施工要点）：本服务**不自行建表**——三表（memories / knowledge_collections /
memory_categories）由 **backend 的 alembic 迁移**创建，共用同一个 Postgres 库与 user_id 口径。
本 Base 只用于 ORM 读写声明。
"""
from __future__ import annotations

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """项目自有声明基类（SQLAlchemy 2.0 风格）。"""


__all__ = ["Base"]
