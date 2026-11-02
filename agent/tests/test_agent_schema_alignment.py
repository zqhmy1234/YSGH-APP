"""Agent 记忆域三表的 **ORM ↔ 真实库** 对拍守卫（2026-10-06 新增）。

## 为什么必须有这个文件（它曾被"声称存在"）

`backend/migrations/versions/a4b5c6d7e8f9_add_agent_memory_tables.py` 与
`backend/app/db/models/memory_agent.py` 的文件头**都写着**有
`test_agent_schema_alignment.py` 做"漂移守卫"，但全仓搜不到该文件（0 命中）。
也就是说，"同一份 schema 在 agent ORM 与后端镜像 ORM 两处声明、必须同时改"
这件事，此前**没有任何自动守卫**——对齐是靠人肉维持的。

这不是杞人忧天，本轮就实打实踩了两回：

1. 补 `knowledge_collections.mood` 与 `memories.content_hash/dedup_bucket` 时，
   迁移先建了**部分唯一索引**却漏在两处 ORM 的 `__table_args__` 里声明，
   `alembic check` 当场报 `removed index`（autogenerate 会生成 `DROP INDEX`）。
2. 迁移里的 `COMMENT ON` 文本与 ORM 的 `comment=` 不一致，又报 `modify_comment`。

两次都是靠 `alembic check` 兜住的。本文件把这个兜底**固化成测试**，
并额外覆盖 agent 侧 ORM（alembic 只对后端 metadata 做 compare）。

## 覆盖

1. **列对齐**：agent ORM 声明的列 ↔ 真实库的列，双向（多列/缺列都算漂移）。
2. **索引对齐**：`uq_memories_user_hash_bucket` 必须在**真实库**与 **两处 ORM** 都存在
   （缺 ORM 声明 ⇒ autogenerate 生成 DROP INDEX，正是踩过的坑）。
3. **两处 ORM 互相一致**：agent `shared/model.py` 与 backend `memory_agent.py`
   对同一张表声明的列集合必须相同（这是"两处必须同时改"的直接检验）。
4. **类型对齐**（宽松）：关键列的类型族一致（防 `String` ↔ `Text` 这类静默漂移）。

## 失败时怎么修

按 `a4b5c6d7e8f9` 文件头的规矩：**迁移与两处 ORM 必须同时改**。
只改迁移 ⇒ 本文件的"索引/列对齐"变红；只改一处 ORM ⇒ "两处 ORM 互相一致"变红。

## 运行条件

需要真实 Postgres（连不上则 skip，不产生假红——本机 Docker 容器经常没起）。
不检查数据，只检查结构。
"""
from __future__ import annotations

import re

import pytest
from sqlalchemy import inspect

from storage.database.db import get_db_url
from storage.database.shared.model import Base as AgentBase

TABLES = ("memory_categories", "memories", "knowledge_collections")
# agent ORM 与 backend 镜像 ORM 必须一致的表（镜像侧按文件路径读，避免拉起 backend app）
BACKEND_MODEL_FILE = "backend/app/db/models/memory_agent.py"


@pytest.fixture(scope="module")
def inspector():
    try:
        from sqlalchemy import create_engine

        engine = create_engine(get_db_url(), pool_pre_ping=True)
        insp = inspect(engine)
        # 探活：连不上就 skip（不产生假红）
        insp.get_table_names()
        return insp
    except Exception as exc:  # noqa: BLE001 —— 环境性缺失不是代码缺陷
        pytest.skip(f"连不上 Postgres，无法做结构对拍：{type(exc).__name__}")


def _agent_columns(table: str) -> set[str]:
    return {c.name for c in AgentBase.metadata.tables[table].columns}


def test_agent_tables_exist_in_db(inspector):
    """三张表必须在真实库里存在（迁移没跑/被 DROP 都会红）。"""
    existing = set(inspector.get_table_names())
    missing = [t for t in TABLES if t not in existing]
    assert not missing, f"库里缺表：{missing}（alembic upgrade head 了吗？）"


@pytest.mark.parametrize("table", TABLES)
def test_columns_match_db(inspector, table: str):
    """agent ORM 声明的列 ↔ 真实库的列，**双向**一致。

    · ORM 多声明 ⇒ 查询/写入会报"列不存在"（历史上 `knowledge_collections.mood`
      就是这样让带情绪的收藏必失败的）
    · ORM 少声明 ⇒ 写入丢失数据、读取拿不到列
    """
    orm_cols = _agent_columns(table)
    db_cols = {c["name"] for c in inspector.get_columns(table)}
    assert orm_cols == db_cols, (
        f"{table} 列漂移\n"
        f"  ORM 多声明（库里没有）: {sorted(orm_cols - db_cols)}\n"
        f"  库里多出（ORM 未声明）: {sorted(db_cols - orm_cols)}"
    )


def test_idempotency_index_exists_in_db(inspector):
    """判重幂等索引必须在真实库存在，且谓词与迁移一致。

    迁移 `f2c3d4e5a6b7` 建的部分唯一索引关掉了 `save_memory` 的判重 TOCTOU
    （实测 12 线程并发写同内容只落 1 行）。它若消失，并发重复会**无声回归**。
    """
    idx = {i["name"]: i for i in inspector.get_indexes("memories")}
    assert "uq_memories_user_hash_bucket" in idx, (
        "memories 上缺幂等唯一索引 ⇒ 判重 TOCTOU 回归（并发下同内容会写多条）"
    )
    cols = list(idx["uq_memories_user_hash_bucket"]["column_names"])
    assert cols == ["user_id", "content_hash", "dedup_bucket"], f"索引列序漂移：{cols}"


def test_idempotency_index_declared_in_agent_orm():
    """索引必须在 agent ORM 的 `__table_args__` 里声明。

    实测踩过：迁移建了索引、ORM 没声明 ⇒ `alembic check` 报 `removed index`、
    `--autogenerate` 会生成 `DROP INDEX`。
    """
    idx = {i.name for i in AgentBase.metadata.tables["memories"].indexes}
    assert "uq_memories_user_hash_bucket" in idx, (
        "agent ORM 未声明幂等索引 ⇒ autogenerate 会生成 DROP INDEX"
    )


def test_backend_mirror_model_stays_in_sync():
    """两处 ORM 对同一张表声明的列必须相同（"两处必须同时改"的直接检验）。

    用**文本解析**而不是 import：backend 的模型走它自己的 Base/约定，
    在 agent 的测试进程里 import 它会拉起 backend 的包结构，得不偿失。
    这里解析 `mapped_column` 前的属性名，足够发现"一边加了列另一边没加"。
    """
    from pathlib import Path

    # ⚠️ 路径层级：tests/ → agent/ → 仓库根。曾算成 parents[3] 落到盘根，
    # 于是本守卫被静默 skip —— **守卫"悄悄不生效"比没有守卫更危险**，
    # 故"仓库里有 backend/ 却找不到镜像模型"要硬失败，而不是 skip。
    repo_root = Path(__file__).resolve().parents[2]
    backend_file = repo_root / BACKEND_MODEL_FILE
    if not backend_file.exists():
        if (repo_root / "backend").is_dir():
            pytest.fail(f"仓库有 backend/ 但找不到镜像模型 {BACKEND_MODEL_FILE} —— 守卫失效，请核对路径")
        pytest.skip(f"本仓没有 backend/（部署形态不同），跳过后端镜像对拍")
    text = backend_file.read_text(encoding="utf-8")

    def _declared_cols(cls_name: str) -> set[str]:
        # ⚠️ 必须用**词边界**定位 class：`text.index("class Memory")` 会命中
        # `class MemoryCategory`（前缀匹配），于是把分类表的列当成记忆表的列，
        # 报出一条看似荒谬实则真实的"漂移"（实测踩过）。
        m = re.search(rf"^class {cls_name}\b", text, re.MULTILINE)
        assert m, f"backend 模型里找不到 class {cls_name}"
        start = m.start()
        end = text.find("\nclass ", start + 1)
        body = text[start:end if end > 0 else len(text)]
        cols: set[str] = set()
        for line in body.splitlines():
            stripped = line.strip()
            if ": Mapped[" in stripped and " = mapped_column" in stripped:
                cols.add(stripped.split(":", 1)[0].strip())
        return cols

    agent_by_table = {t: _agent_columns(t) for t in TABLES}
    backend_cls = {
        "memory_categories": "MemoryCategory",
        "memories": "Memory",
        "knowledge_collections": "KnowledgeCollection",
    }
    for table, cls in backend_cls.items():
        backend_cols = _declared_cols(cls)
        assert backend_cols, f"backend/{cls} 没解析到任何列（模型文件结构变了？）"
        only_agent = agent_by_table[table] - backend_cols
        only_backend = backend_cols - agent_by_table[table]
        assert not only_agent and not only_backend, (
            f"{table} 两处 ORM 漂移（迁移头警告过的那种）\n"
            f"  仅 agent 有: {sorted(only_agent)}\n"
            f"  仅 backend 有: {sorted(only_backend)}"
        )


@pytest.mark.parametrize(
    ("table", "column", "family"),
    [
        ("memories", "content", "text"),
        ("memories", "user_id", "string"),
        ("memories", "topics", "json"),
        ("memories", "created_at", "datetime"),
        ("knowledge_collections", "content_type", "string"),
        ("knowledge_collections", "importance", "integer"),
    ],
)
def test_key_column_types_match_db(inspector, table: str, column: str, family: str):
    """关键列的类型族不能漂移（`String`↔`Text`、`Integer`↔`BigInteger` 这类最隐蔽）。"""
    col = next(c for c in inspector.get_columns(table) if c["name"] == column)
    orm_col = AgentBase.metadata.tables[table].columns[column]
    orm_family = orm_col.type.python_type.__name__.lower()
    db_type = str(col["type"]).lower()
    if family == "text":
        assert "text" in db_type, f"{table}.{column} 库里是 {db_type}"
        assert "str" in orm_family
    elif family == "string":
        assert "varchar" in db_type or "character" in db_type, f"{table}.{column} 库里是 {db_type}"
        assert "str" in orm_family
    elif family == "json":
        assert "json" in db_type, f"{table}.{column} 库里是 {db_type}"
    elif family == "datetime":
        assert "timestamp" in db_type, f"{table}.{column} 库里是 {db_type}"
        assert "datetime" in orm_family
    elif family == "integer":
        assert "int" in db_type, f"{table}.{column} 库里是 {db_type}"
        assert "int" in orm_family
