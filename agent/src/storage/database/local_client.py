"""PostgREST 兼容薄层（SQLAlchemy 支撑）——去 Supabase 的**单点替换**。

为什么做成兼容层而不是逐个改写调用点：
  上游 25+ 处业务调用都写成 `client.table("memories").select(...).eq(...).execute()` 形态，
  散布在 `tools/memory_tools.py`、`tools/url_fetch_tools.py`、`web/api_routes.py`、
  `services/{wechat,daily_review}_service.py`。且**它们全部经 `get_supabase_client()` 单入口**
  （已核：`_get_client()` 一律转调该函数）。故实现一个 PostgREST 形状的本地客户端、
  替换那个入口即可——**业务逻辑零改动**，风险集中在本文件（可单测）。

与 PostgREST 的语义对齐（按上游实际用到的子集）：
  select / eq / neq / gte / lte / gt / lt / in_ / order / limit / range / maybe_single / single
  insert / update / delete，`count="exact"`，以及关系嵌入 `memory_categories(name)` → 嵌套 dict。
  **超出子集的方法不实现**（调用时报错并提示），避免"看似支持、静默走错"。

刻意比 PostgREST **更严**的一点：列名会对着 ORM 校验，写错列名**立即报错**（PostgREST 也会 400，
但本地实现若不校验就会静默返回空——那是最难查的一类）。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import create_engine, delete, func, insert, select, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session, sessionmaker
from storage.database.shared.model import KnowledgeCollections, Memories, MemoryCategories

logger = logging.getLogger("yishu.agent.db")

# 表名 → ORM 类（三表由 backend 的 alembic 迁移创建，见 backend/migrations/versions/a4b5c6d7e8f9_*.py）
_TABLES: dict[str, Any] = {
    "memories": Memories,
    "knowledge_collections": KnowledgeCollections,
    "memory_categories": MemoryCategories,
}

def _db_url() -> str:
    """连接串：优先 DATABASE_URL（本服务 .env 口径），兼容上游的 PGDATABASE_URL。"""
    url = os.getenv("DATABASE_URL") or os.getenv("PGDATABASE_URL")
    if not url:
        raise RuntimeError(
            "未配置 DATABASE_URL（agent/.env）—— 数据落我们自己的 Postgres，连接串必填"
        )
    return url


@lru_cache(maxsize=1)
def _session_factory() -> sessionmaker[Session]:
    """进程内单例连接池。

    用 `lru_cache` 而不是模块级 global：语义相同（首次调用建、后续复用），
    但避免 `global` 语句（ruff PLW0603 不建议），也少一处可变模块态。
    """
    engine = create_engine(_db_url(), pool_pre_ping=True, pool_size=5, max_overflow=5)
    return sessionmaker(bind=engine, expire_on_commit=False)


class LocalResponse:
    """对齐 supabase `APIResponse` 的最小形状：`.data` 与 `.count`。"""

    def __init__(self, data: Any, count: int | None = None) -> None:
        self.data = data
        self.count = count

    def __repr__(self) -> str:  # 便于调试
        size = len(self.data) if isinstance(self.data, list) else (1 if self.data else 0)
        return f"<LocalResponse rows={size} count={self.count}>"


def _model_for(table: str) -> Any:
    model = _TABLES.get(table)
    if model is None:
        raise ValueError(f"未知表 {table!r}（本薄层仅支持 {sorted(_TABLES)}）")
    return model


def _parse_embed(spec: str) -> tuple[str, list[str]] | None:
    """解析关系嵌入写法 `memory_categories(name, id)` → ('memory_categories', ['name','id'])。"""
    if "(" not in spec:
        return None
    name = spec[: spec.index("(")].strip()
    inner = spec[spec.index("(") + 1 : spec.rindex(")")]
    cols = [c.strip() for c in inner.split(",") if c.strip()]
    return name, cols


class LocalTableQuery:
    """`client.table(x)` 返回的查询构造器（链式，末端 `.execute()`）。"""

    def __init__(self, table: str) -> None:
        self.table = table
        self.model = _model_for(table)
        self.action = "select"
        self._payload: Any = None
        self._select: list[str] = ["*"]
        self._count_exact = False
        self._filters: list[tuple[str, str, Any]] = []
        self._order: list[tuple[str, bool]] = []
        self._limit: int | None = None
        self._offset: int | None = None
        self._single: str | None = None

    # ── 动作 ──
    def select(self, columns: str = "*", count: str | None = None) -> LocalTableQuery:
        self.action = "select"
        self._select = [c.strip() for c in columns.split(",")] if columns else ["*"]
        self._count_exact = count == "exact"
        return self

    def insert(self, payload: Any) -> LocalTableQuery:
        self.action = "insert"
        self._payload = payload
        return self

    def update(self, payload: dict[str, Any]) -> LocalTableQuery:
        self.action = "update"
        self._payload = payload
        return self

    def delete(self) -> LocalTableQuery:
        self.action = "delete"
        return self

    # ── 过滤 ──
    def _add(self, column: str, op: str, value: Any) -> LocalTableQuery:
        self._filters.append((column, op, value))
        return self

    def eq(self, column: str, value: Any) -> LocalTableQuery:
        return self._add(column, "eq", value)

    def neq(self, column: str, value: Any) -> LocalTableQuery:
        return self._add(column, "neq", value)

    def gt(self, column: str, value: Any) -> LocalTableQuery:
        return self._add(column, "gt", value)

    def gte(self, column: str, value: Any) -> LocalTableQuery:
        return self._add(column, "gte", value)

    def lt(self, column: str, value: Any) -> LocalTableQuery:
        return self._add(column, "lt", value)

    def lte(self, column: str, value: Any) -> LocalTableQuery:
        return self._add(column, "lte", value)

    def in_(self, column: str, values: list[Any]) -> LocalTableQuery:
        return self._add(column, "in", values)

    # ── 排序/分页/单行 ──
    def order(self, column: str, desc: bool = False) -> LocalTableQuery:
        self._order.append((column, desc))
        return self

    def limit(self, count: int) -> LocalTableQuery:
        self._limit = count
        return self

    def range(self, start: int, end: int) -> LocalTableQuery:
        self._offset = start
        self._limit = end - start + 1
        return self

    def maybe_single(self) -> LocalTableQuery:
        self._single = "maybe"
        return self

    def single(self) -> LocalTableQuery:
        self._single = "one"
        return self

    # ── 内部：列与值 ──
    def _relationship(self, relation: str):
        """按**关系的目标表名**解析关系属性。

        为什么不能直接 `getattr(model, relation)`：PostgREST 嵌入写的是**表名**
        （`memory_categories(name)`），而 ORM 关系属性名可以是别的（本仓是 `category`）。
        故按 target 表名反查（实测踩到：直接 getattr 得 None → 嵌入恒为 None）。
        """
        mapper = sa_inspect(self.model)
        for rel in mapper.relationships:
            if rel.target.name == relation:
                return rel
        return None

    def _column(self, name: str):
        embed = _parse_embed(name)
        if embed is not None:
            relation = embed[0]
            if self._relationship(relation) is None:
                raise ValueError(f"表 {self.table} 上不存在指向 {relation!r} 的关系（嵌入写法有误？）")
            return None  # 关系列在 execute 里单独处理
        attr = getattr(self.model, name, None)
        if attr is None:
            raise ValueError(f"表 {self.table} 上不存在列 {name!r}（列名写错会静默返回空，故显式拒绝）")
        return attr

    def _coerce(self, column_name: str, value: Any) -> Any:
        """ISO 字符串 → datetime（上游大量 `.gte('created_at', datetime.utcnow().isoformat())`）；JSON 列容忍字符串。"""
        if value is None or not isinstance(value, str):
            return value
        col_type = self.model.__table__.columns.get(column_name)
        if col_type is None:
            return value
        type_name = type(col_type.type).__name__.lower()
        if "datetime" in type_name or "date" in type_name:
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return value
        if type_name == "json":
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return value
        return value

    def _conditions(self) -> list[Any]:
        conds: list[Any] = []
        for column, op, raw in self._filters:
            attr = self._column(column)
            if attr is None:  # 关系列过滤（上游未用到，显式拒绝更安全）
                raise ValueError(f"暂不支持按关系列 {column!r} 过滤")
            value = self._coerce(column, raw)
            if op == "eq":
                conds.append(attr.is_(None) if value is None else attr == value)
            elif op == "neq":
                conds.append(attr.is_not(None) if value is None else attr != value)
            elif op == "gt":
                conds.append(attr > value)
            elif op == "gte":
                conds.append(attr >= value)
            elif op == "lt":
                conds.append(attr < value)
            elif op == "lte":
                conds.append(attr <= value)
            elif op == "in":
                conds.append(attr.in_(value))
        return conds

    # ── 内部：行 → dict（含关系嵌入）──
    def _row_to_dict(self, session: Session, row: Any) -> dict[str, Any]:
        out: dict[str, Any] = {}
        wanted: list[str] = []
        for spec in self._select:
            if spec == "*":
                wanted = [c.name for c in self.model.__table__.columns]
                break
            embed = _parse_embed(spec)
            if embed is None:
                wanted.append(spec)
        for name in wanted:
            if not hasattr(row, name):
                raise ValueError(f"表 {self.table} 上不存在列 {name!r}")
            out[name] = getattr(row, name)
        if not wanted and "*" not in self._select:
            out = {c.name: getattr(row, c.name) for c in self.model.__table__.columns}
        # 关系嵌入：memory_categories(name) → {"memory_categories": {"name": ...}}（无关联时 None）
        for spec in self._select:
            embed = _parse_embed(spec)
            if embed is None:
                continue
            relation, cols = embed
            rel = self._relationship(relation)
            related = getattr(row, rel.key) if rel is not None else None
            if related is None:
                out[relation] = None
            else:
                out[relation] = {c: getattr(related, c) for c in (cols or ["id"])}
        return out

    def execute(self) -> LocalResponse:
        factory = _session_factory()
        with factory() as session:
            if self.action == "select":
                conds = self._conditions()
                stmt = select(self.model)
                if conds:
                    stmt = stmt.where(*conds)
                for column, desc in self._order:
                    attr = self._column(column)
                    stmt = stmt.order_by(attr.desc() if desc else attr.asc())
                if self._limit is not None:
                    stmt = stmt.limit(self._limit)
                if self._offset is not None:
                    stmt = stmt.offset(self._offset)
                rows = session.execute(stmt).scalars().all()
                data = [self._row_to_dict(session, r) for r in rows]
                count = None
                if self._count_exact:
                    cstmt = select(func.count()).select_from(self.model)
                    if conds:
                        cstmt = cstmt.where(*conds)
                    count = session.execute(cstmt).scalar()
                if self._single:
                    if len(data) > 1 and self._single == "one":
                        raise ValueError(f"{self.table} 命中 {len(data)} 行，单行查询要求唯一")
                    return LocalResponse(data=data[0] if data else None, count=count)
                return LocalResponse(data=data, count=count)

            if self.action == "insert":
                payloads = self._payload if isinstance(self._payload, list) else [self._payload]
                payloads = [self._normalize_payload(p) for p in payloads]
                stmt = insert(self.model).returning(self.model)
                result = session.execute(stmt, payloads)
                data = [self._row_to_dict(session, r) for r in result.scalars().all()]
                session.commit()
                return LocalResponse(data=data)

            if self.action == "update":
                conds = self._conditions()
                if not conds:
                    raise ValueError(f"update {self.table} 未带条件 —— 拒绝全表更新")
                stmt = update(self.model).where(*conds).values(**self._normalize_payload(self._payload))
                stmt = stmt.returning(self.model)
                data = [self._row_to_dict(session, r) for r in session.execute(stmt).scalars().all()]
                session.commit()
                return LocalResponse(data=data)

            if self.action == "delete":
                conds = self._conditions()
                if not conds:
                    raise ValueError(f"delete {self.table} 未带条件 —— 拒绝全表删除")
                stmt = delete(self.model).where(*conds).returning(self.model)
                data = [self._row_to_dict(session, r) for r in session.execute(stmt).scalars().all()]
                session.commit()
                return LocalResponse(data=data)

        raise ValueError(f"未实现的动作 {self.action!r}")

    def _normalize_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for key, value in payload.items():
            attr = getattr(self.model, key, None)
            if attr is None:
                raise ValueError(f"表 {self.table} 上不存在列 {key!r}（写入列名写错会被静默忽略）")
            out[key] = self._coerce(key, value)
        return out


class LocalClient:
    """与 supabase `Client` 的 `table()` 子集同形。"""

    def table(self, name: str) -> LocalTableQuery:
        return LocalTableQuery(name)


def create_local_client(*_args: Any, **_kwargs: Any) -> LocalClient:
    """签名兼容 `create_client(url, key, options=...)`（参数被刻意忽略：本地库不需要 URL/key）。"""
    logger.info("本地数据层已启用（SQLAlchemy → 我们自己的 Postgres；Supabase 调用点零改动）")
    return LocalClient()
