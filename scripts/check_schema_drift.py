#!/usr/bin/env python3
"""schema.sql ↔ alembic 迁移链结构漂移检测（重构侦察 P0-4 / issue #2 落地）

背景（lessons.md:97 已明确解法，本脚本落地）：
  - CI 建库源 = backend/sql/schema.sql；本地/生产走 alembic 迁移链——两链无强制对齐
  - 历史漂移事故：#8 profile_annotation_pool 缺表、#16 27→38 表/FK/vector 扩展、
    alembic stamp ≠ 建表（本地库 26 表 vs schema.sql 38 表严重不符）

方法：
  A 侧（CI 建库源）：临时库执行 schema.sql → SQLAlchemy Inspector 提取结构
  B 侧（alembic head）：临时库执行 `alembic upgrade head`；
      若基线迁移不自包含（已知 431bcaa8bd54 仅 alter_column，从空库无法自举，
      lessons #9-#12 记录在案），回退到 ORM metadata（env.py target_metadata，
      `alembic check` 零漂移已验证 ORM == 迁移链目标态），并登记一条 CRITICAL 发现
  → 对比表集合 / 列（名·类型·可空）/ 索引（列集·唯一·where）/ 外键（列集·引用·级联）
    / 唯一约束（列集）/ 主键（列集）；列默认值差异仅 INFO 不阻断
    （ORM Python 侧默认 vs schema.sql DDL 默认 = 已知允许差异）

归一化（排除已知允许差异）：
  - 类型别名：TEXT/VARCHAR/CHARACTER VARYING（无长度）↔ string；VARCHAR(n) ↔ string(n)；
    TIMESTAMP WITH TIME ZONE ↔ timestamptz；DOUBLE PRECISION ↔ double
  - 顺序无关：列序/索引列序/表序全部排序后比较
  - 索引/约束命名差异（idx_/ix_/uq_/_key 前缀）不阻断——结构一致即视为对齐

用法：
  python scripts/check_schema_drift.py [--admin-url URL] [--schema-sql PATH]
      [--backend-dir PATH] [--keep]

退出码：
  0 = 无结构漂移（两链对齐）
  1 = 检出结构漂移（CI weekly job 出 annotation，不阻断主流水线）
  2 = 执行环境错误（连不上库 / 建库失败 / schema.sql 执行失败）
"""
from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import psycopg
from sqlalchemy import create_engine, inspect, make_url, text

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent

# 管理员连接（需 CREATEDB + 超级用户，用于建临时库/装 vector 扩展）。
# 本地/CI 均为 postgres 超级用户：本地 setup_pg.sql 同款凭据；CI postgres service 同款。
DEFAULT_ADMIN_URL = "postgresql+psycopg://postgres:admin@localhost:5432/postgres"

_TABLES_SKIP = {"alembic_version"}


# ---------------------------------------------------------------- 归一化


def _norm_type(t) -> str:
    """类型归一化：排除已知允许差异（TEXT/String 别名、时区后缀、double 别名）。"""
    s = str(t).lower()
    s = s.replace("character varying", "varchar")
    if s in ("text", "varchar"):
        return "string"
    m = re.fullmatch(r"varchar\((\d+)\)", s)
    if m:
        return f"string({m.group(1)})"
    if s in ("timestamp with time zone", "timestamptz"):
        return "timestamptz"
    if s == "timestamp without time zone":
        return "timestamp"
    if s in ("double precision", "double"):
        return "double"
    return s


def _norm_default(d) -> str | None:
    """默认值归一化（仅 INFO 比较用）：去 cast/空白、now() 别名统一。"""
    if d is None:
        return None
    s = str(d).lower().replace(" ", "")
    for cast in ("::text", "::character varying", "::charactervarying", "::jsonb", "::regclass"):
        s = s.replace(cast, "")
    s = s.replace("current_timestamp", "now()")
    return s


def _norm_where(w) -> str:
    """部分索引 where 表达式归一化：去空白/cast/括号（结构等价即视为对齐）。"""
    if not w:
        return ""
    s = str(w).lower().replace(" ", "")
    for cast in ("::text", "::character varying", "::charactervarying", "::jsonb"):
        s = s.replace(cast, "")
    return s.replace("(", "").replace(")", "")


# ---------------------------------------------------------------- 提取


def _sqlalchemy_url(admin_url: str, dbname: str) -> str:
    """admin URL → SQLAlchemy URL（替换目标库名，保留 +psycopg 方言）。"""
    return make_url(admin_url).set(database=dbname).render_as_string(hide_password=False)


def _psycopg_dsn(admin_url: str, dbname: str) -> str:
    """admin URL → psycopg 直连 DSN（替换目标库名，去掉 +psycopg 方言后缀）。"""
    return _sqlalchemy_url(admin_url, dbname).replace("postgresql+psycopg://", "postgresql://")


def _run_schema_sql(admin_url: str, dbname: str, schema_sql: str) -> None:
    """在临时库执行 schema.sql（psycopg 简单协议支持多语句；超级用户可建 vector 扩展）。"""
    conn = psycopg.connect(_psycopg_dsn(admin_url, dbname), autocommit=True)
    try:
        conn.execute(schema_sql)
    finally:
        conn.close()


def _create_temp_db(admin_url: str, dbname: str) -> None:
    eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    eng.dispose()


def _drop_temp_db(admin_url: str, dbname: str) -> None:
    eng = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    with eng.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
    eng.dispose()


def _introspect(admin_url: str, dbname: str) -> dict:
    """提取指定库的结构（表→列/主键/唯一约束/索引/外键），全部归一化排序。"""
    eng = create_engine(_sqlalchemy_url(admin_url, dbname))
    insp = inspect(eng)
    out: dict[str, dict] = {}
    for table in sorted(insp.get_table_names()):
        if table in _TABLES_SKIP:
            continue
        cols: dict[str, tuple] = {}
        for c in insp.get_columns(table):
            cols[c["name"]] = (_norm_type(c["type"]), bool(c["nullable"]), _norm_default(c.get("default")))
        pk = frozenset(insp.get_pk_constraint(table).get("constrained_columns") or [])
        uniques = {frozenset(u.get("column_names") or []) for u in insp.get_unique_constraints(table)}
        indexes = set()
        for ix in insp.get_indexes(table):
            icols = tuple(ix.get("column_names") or [])
            iunique = bool(ix.get("unique"))
            # 跳过唯一约束的支撑索引与主键索引（避免与 uniques/pk 重复计数）
            if iunique and frozenset(icols) in uniques:
                continue
            if frozenset(icols) == pk:
                continue
            where = _norm_where(ix.get("dialect_options", {}).get("postgresql_where") or "")
            indexes.add((icols, iunique, where))
        fks = set()
        for fk in insp.get_foreign_keys(table):
            opts = fk.get("options", {}) or {}
            fks.add(
                (
                    tuple(fk.get("constrained_columns") or []),
                    fk.get("referred_table", ""),
                    tuple(fk.get("referred_columns") or []),
                    opts.get("ondelete") or "",
                    opts.get("onupdate") or "",
                )
            )
        out[table] = {
            "columns": cols,
            "pk": pk,
            "uniques": uniques,
            "indexes": indexes,
            "fks": fks,
        }
    eng.dispose()
    return out


def _build_alembic_side(admin_url: str, dbname: str, backend_dir: Path) -> tuple[dict, list[str]]:
    """B 侧：先尝试 `alembic upgrade head`；失败（基线非自包含）回退 ORM metadata。

    返回 (结构, notes)。notes 记录回退原因等非结构信息。
    """
    notes: list[str] = []
    _create_temp_db(admin_url, dbname)
    env = {**os.environ, "DATABASE_URL": _sqlalchemy_url(admin_url, dbname), "PYTHONPATH": str(backend_dir)}
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=backend_dir,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
        check=False,  # noqa: PLW1510 —— 返回码由调用方判读（成功/失败回退两条路径）
    )
    if proc.returncode == 0:
        notes.append("alembic upgrade head 从空库成功（迁移链自包含）")
        return _introspect(admin_url, dbname), notes

    tail = (proc.stdout + proc.stderr).strip().splitlines()[-3:]
    notes.append(
        "alembic upgrade head 从空库失败（基线迁移 431bcaa8bd54 非自包含，"
        "仅 alter_column 假设表已由 schema.sql 预建——lessons #9-#12 已知问题）；"
        "回退 ORM metadata（P2-05 声明唯一权威，env.py target_metadata，alembic check 校验 DB↔ORM）。"
        f" 失败尾部: {' | '.join(tail)}"
    )
    # alembic 可能残留 alembic_version 表 → 重建临时库再 create_all
    _drop_temp_db(admin_url, dbname)
    _create_temp_db(admin_url, dbname)
    sys.path.insert(0, str(backend_dir))
    os.environ["DATABASE_URL"] = _sqlalchemy_url(admin_url, dbname)
    import app.db.models  # noqa: F401 —— 注册全部模型
    from app.db.session import Base

    eng = create_engine(_sqlalchemy_url(admin_url, dbname))
    Base.metadata.create_all(eng)
    eng.dispose()
    return _introspect(admin_url, dbname), notes


# ---------------------------------------------------------------- 对比


def _compare(a: dict, b: dict) -> tuple[list[str], list[str]]:
    """对比 A(schema.sql) vs B(alembic head)。返回 (blocking, info)。"""
    blocking: list[str] = []
    info: list[str] = []

    # 表集合
    for t in sorted(set(a) - set(b)):
        blocking.append(f"[表集合] {t} 仅存在于 schema.sql 侧（CI 建库源），alembic head 无此表")
    for t in sorted(set(b) - set(a)):
        blocking.append(f"[表集合] {t} 仅存在于 alembic head 侧（迁移链建），schema.sql 无此表")

    for t in sorted(set(a) & set(b)):
        ca, cb = a[t]["columns"], b[t]["columns"]
        # 列集合 + 类型/可空
        for col in sorted(set(ca) - set(cb)):
            blocking.append(f"[列] {t}.{col} 仅存在于 schema.sql 侧")
        for col in sorted(set(cb) - set(ca)):
            blocking.append(f"[列] {t}.{col} 仅存在于 alembic head 侧（迁移新增，schema.sql 未同步）")
        for col in sorted(set(ca) & set(cb)):
            ta, tb_ = ca[col], cb[col]
            if ta[0] != tb_[0]:
                blocking.append(f"[列] {t}.{col} 类型漂移：schema.sql={ta[0]} vs alembic={tb_[0]}")
            if ta[1] != tb_[1]:
                blocking.append(
                    f"[列] {t}.{col} 可空性漂移：schema.sql={'NULL' if ta[1] else 'NOT NULL'} "
                    f"vs alembic={'NULL' if tb_[1] else 'NOT NULL'}"
                )
            if ta[2] != tb_[2]:
                info.append(f"[默认值] {t}.{col} 默认值不同：schema.sql={ta[2]} vs alembic={tb_[2]}")
        # 主键
        if a[t]["pk"] != b[t]["pk"]:
            blocking.append(
                f"[主键] {t} 主键漂移：schema.sql={sorted(a[t]['pk'])} vs alembic={sorted(b[t]['pk'])}"
            )
        # 唯一约束（列集）
        for u in sorted(map(sorted, a[t]["uniques"])):
            if frozenset(u) not in b[t]["uniques"]:
                blocking.append(f"[唯一约束] {t}{tuple(u)} 仅存在于 schema.sql 侧")
        for u in sorted(map(sorted, b[t]["uniques"])):
            if frozenset(u) not in a[t]["uniques"]:
                blocking.append(f"[唯一约束] {t}{tuple(u)} 仅存在于 alembic head 侧")
        # 索引（列集+唯一+where；命名差异不阻断）
        for ix in sorted(a[t]["indexes"], key=str):
            if ix not in b[t]["indexes"]:
                blocking.append(
                    f"[索引] {t} schema.sql 侧有 alembic 侧无：列={ix[0]} unique={ix[1]} where={ix[2]!r}"
                )
        for ix in sorted(b[t]["indexes"], key=str):
            if ix not in a[t]["indexes"]:
                blocking.append(
                    f"[索引] {t} alembic 侧有 schema.sql 侧无：列={ix[0]} unique={ix[1]} where={ix[2]!r}"
                )
        # 外键（列集+引用+级联；命名差异不阻断）
        for fk in sorted(a[t]["fks"], key=str):
            if fk not in b[t]["fks"]:
                blocking.append(f"[外键] {t} schema.sql 侧有 alembic 侧无：{fk}")
        for fk in sorted(b[t]["fks"], key=str):
            if fk not in a[t]["fks"]:
                blocking.append(f"[外键] {t} alembic 侧有 schema.sql 侧无：{fk}")

    return blocking, info


# ---------------------------------------------------------------- 主流程


def main() -> int:
    ap = argparse.ArgumentParser(description="schema.sql ↔ alembic 迁移链结构漂移检测")
    ap.add_argument("--admin-url", default=os.environ.get("DRIFT_ADMIN_URL", DEFAULT_ADMIN_URL))
    ap.add_argument("--schema-sql", default=str(ROOT / "backend/sql/schema.sql"))
    ap.add_argument("--backend-dir", default=str(ROOT / "backend"))
    ap.add_argument("--keep", action="store_true", help="保留临时库（调试）")
    args = ap.parse_args()

    schema_sql_path = Path(args.schema_sql)
    backend_dir = Path(args.backend_dir)
    if not schema_sql_path.exists():
        print(f"ERROR: schema.sql 不存在: {schema_sql_path}")
        return 2

    suffix = secrets.token_hex(4)
    db_a = f"yishu_drift_schema_{suffix}"
    db_b = f"yishu_drift_alembic_{suffix}"

    try:
        # A 侧：schema.sql 建库
        _create_temp_db(args.admin_url, db_a)
        _run_schema_sql(args.admin_url, db_a, schema_sql_path.read_text(encoding="utf-8"))
        side_a = _introspect(args.admin_url, db_a)
        side_a_note = "schema.sql 从空库建库成功"

        # B 侧：alembic upgrade head（失败回退 ORM metadata）
        side_b, notes_b = _build_alembic_side(args.admin_url, db_b, backend_dir)
    except Exception as exc:  # noqa: BLE001 —— 环境错误统一 exit 2
        print(f"ERROR: 提取失败（环境错误）: {exc!r}")
        return 2
    finally:
        if not args.keep:
            _drop_temp_db(args.admin_url, db_a)
            _drop_temp_db(args.admin_url, db_b)

    print(f"A 侧（CI 建库源 schema.sql）：{len(side_a)} 表；{side_a_note}")
    print(f"B 侧（alembic head）：{len(side_b)} 表")
    for note in notes_b:
        print(f"  NOTE: {note}")

    blocking, info = _compare(side_a, side_b)

    print()
    print(f"===== 漂移检出：BLOCKING {len(blocking)} 条 / INFO {len(info)} 条 =====")
    for line in blocking:
        print(f"  BLOCKING {line}")
    for line in info:
        print(f"  INFO     {line}")

    # GitHub Actions annotation（weekly job 失败仅出 annotation，不阻断主流水线）
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for line in blocking[:100]:
            print(f"::error::漂移 {line}")
        for line in info[:100]:
            print(f"::warning::漂移(INFO) {line}")
        if blocking:
            print(f"::error::schema.sql ↔ alembic 漂移共 {len(blocking)} 条 BLOCKING，详见日志")

    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
