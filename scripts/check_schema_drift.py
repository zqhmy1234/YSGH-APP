#!/usr/bin/env python3
"""Compare fresh ORM and Alembic schemas in isolated temporary databases.

The historical baseline cannot upgrade an empty database by itself. B-1 adds
an empty-database bootstrap in Alembic env.py. This check requires that path
to succeed; it never falls back to ORM after an Alembic failure.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

# D14-11（B10-n）：UTF-8 兜底唯一实现（scripts/gate_io.py）
from gate_io import force_utf8  # noqa: E402
from sqlalchemy import create_engine, inspect, make_url, text

force_utf8()

ROOT = Path(__file__).resolve().parent.parent

# 管理员连接（需 CREATEDB + 超级用户，用于建临时库/装 vector 扩展）。
# 凭据**不入仓库**（原内联默认 `postgres:admin` 已移除，2026-09-24 重构波 B7）：
#   优先 `--admin-url`，其次环境变量 `DRIFT_ADMIN_URL`；两者皆空则显式报错退出。
#   CI 走显式 `--admin-url`（见 .github/workflows/ci.yml）；
#   本地示例：$env:DRIFT_ADMIN_URL="postgresql+psycopg://postgres:<你的密码>@localhost:5432/postgres"
DEFAULT_ADMIN_URL = ""

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


def _create_temp_db(admin_url: str, dbname: str) -> None:
    eng = create_engine(admin_url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5})
    with eng.connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    eng.dispose()


def _drop_temp_db(admin_url: str, dbname: str) -> None:
    eng = create_engine(admin_url, isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 5})
    with eng.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{dbname}"'))
    eng.dispose()


def _introspect(admin_url: str, dbname: str) -> dict:
    """提取指定库的结构（表→列/主键/唯一约束/索引/外键），全部归一化排序。"""
    eng = create_engine(_sqlalchemy_url(admin_url, dbname), connect_args={"connect_timeout": 5})
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


def _build_orm_side(admin_url: str, dbname: str, backend_dir: Path) -> dict:
    """Build the authoritative schema directly from ORM metadata."""
    _create_temp_db(admin_url, dbname)
    sys.path.insert(0, str(backend_dir))
    import app.db.models  # noqa: F401 -- register every mapped table
    from app.db.session import Base

    engine = create_engine(_sqlalchemy_url(admin_url, dbname), connect_args={"connect_timeout": 5})
    try:
        Base.metadata.create_all(engine)
    finally:
        engine.dispose()
    return _introspect(admin_url, dbname)


def _build_alembic_side(admin_url: str, dbname: str, backend_dir: Path) -> dict:
    """Require `alembic upgrade head` to bootstrap an empty database."""
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
        timeout=180,
        check=False,
    )
    if proc.returncode:
        # Do not print stderr: configuration errors may include a credential URL.
        raise RuntimeError(f"alembic upgrade head failed (exit {proc.returncode})")
    return _introspect(admin_url, dbname)


# ---------------------------------------------------------------- 对比


def _compare(a: dict, b: dict) -> tuple[list[str], list[str]]:
    """对比 A(ORM) vs B(alembic head)。返回 (blocking, info)。"""
    blocking: list[str] = []
    info: list[str] = []

    # 表集合
    for t in sorted(set(a) - set(b)):
        blocking.append(f"[表集合] {t} 仅存在于 ORM 侧，alembic head 无此表")
    for t in sorted(set(b) - set(a)):
        blocking.append(f"[表集合] {t} 仅存在于 alembic head 侧（迁移链建），ORM 无此表")

    for t in sorted(set(a) & set(b)):
        ca, cb = a[t]["columns"], b[t]["columns"]
        # 列集合 + 类型/可空
        for col in sorted(set(ca) - set(cb)):
            blocking.append(f"[列] {t}.{col} 仅存在于 ORM 侧")
        for col in sorted(set(cb) - set(ca)):
            blocking.append(f"[列] {t}.{col} 仅存在于 alembic head 侧（迁移新增，ORM 未同步）")
        for col in sorted(set(ca) & set(cb)):
            ta, tb_ = ca[col], cb[col]
            if ta[0] != tb_[0]:
                blocking.append(f"[列] {t}.{col} 类型漂移：ORM={ta[0]} vs alembic={tb_[0]}")
            if ta[1] != tb_[1]:
                blocking.append(
                    f"[列] {t}.{col} 可空性漂移：ORM={'NULL' if ta[1] else 'NOT NULL'} "
                    f"vs alembic={'NULL' if tb_[1] else 'NOT NULL'}"
                )
            if ta[2] != tb_[2]:
                blocking.append(f"[默认值] {t}.{col} 默认值不同：ORM={ta[2]} vs alembic={tb_[2]}")
        # 主键
        if a[t]["pk"] != b[t]["pk"]:
            blocking.append(f"[主键] {t} 主键漂移：ORM={sorted(a[t]['pk'])} vs alembic={sorted(b[t]['pk'])}")
        # 唯一约束（列集）
        for u in sorted(map(sorted, a[t]["uniques"])):
            if frozenset(u) not in b[t]["uniques"]:
                blocking.append(f"[唯一约束] {t}{tuple(u)} 仅存在于 ORM 侧")
        for u in sorted(map(sorted, b[t]["uniques"])):
            if frozenset(u) not in a[t]["uniques"]:
                blocking.append(f"[唯一约束] {t}{tuple(u)} 仅存在于 alembic head 侧")
        # 索引（列集+唯一+where；命名差异不阻断）
        for ix in sorted(a[t]["indexes"], key=str):
            if ix not in b[t]["indexes"]:
                blocking.append(f"[索引] {t} ORM 侧有 alembic 侧无：列={ix[0]} unique={ix[1]} where={ix[2]!r}")
        for ix in sorted(b[t]["indexes"], key=str):
            if ix not in a[t]["indexes"]:
                blocking.append(f"[索引] {t} alembic 侧有 ORM 侧无：列={ix[0]} unique={ix[1]} where={ix[2]!r}")
        # 外键（列集+引用+级联；命名差异不阻断）
        for fk in sorted(a[t]["fks"], key=str):
            if fk not in b[t]["fks"]:
                blocking.append(f"[外键] {t} ORM 侧有 alembic 侧无：{fk}")
        for fk in sorted(b[t]["fks"], key=str):
            if fk not in a[t]["fks"]:
                blocking.append(f"[外键] {t} alembic 侧有 ORM 侧无：{fk}")

    return blocking, info


# ---------------------------------------------------------------- 主流程


def main() -> int:
    ap = argparse.ArgumentParser(description="ORM versus fresh Alembic schema drift check")
    ap.add_argument("--admin-url", default=os.environ.get("DRIFT_ADMIN_URL", DEFAULT_ADMIN_URL))
    ap.add_argument("--backend-dir", default=str(ROOT / "backend"))
    ap.add_argument("--keep", action="store_true", help="keep temporary databases for debugging")
    args = ap.parse_args()
    if not args.admin_url:
        print("ERROR: provide --admin-url or DRIFT_ADMIN_URL", file=sys.stderr)
        return 2

    suffix = secrets.token_hex(4)
    db_orm = f"yishu_drift_orm_{suffix}"
    db_alembic = f"yishu_drift_alembic_{suffix}"
    try:
        print("Building ORM temporary database...", flush=True)
        side_orm = _build_orm_side(args.admin_url, db_orm, Path(args.backend_dir))
        print("Building Alembic temporary database...", flush=True)
        side_alembic = _build_alembic_side(args.admin_url, db_alembic, Path(args.backend_dir))
    except Exception as exc:  # noqa: BLE001 -- environment failure is exit 2
        print(f"ERROR: schema build failed: {type(exc).__name__}", file=sys.stderr)
        return 2
    finally:
        if not args.keep:
            for dbname in (db_orm, db_alembic):
                try:
                    _drop_temp_db(args.admin_url, dbname)
                except Exception as exc:  # noqa: BLE001 -- preserve the original failure
                    print(f"WARNING: temporary database cleanup failed: {type(exc).__name__}", file=sys.stderr)

    blocking, info = _compare(side_orm, side_alembic)
    print(f"ORM: {len(side_orm)} tables; Alembic: {len(side_alembic)} tables")
    print(f"BLOCKING {len(blocking)} / INFO {len(info)}")
    for line in blocking:
        print(f"  BLOCKING {line}")
    for line in info:
        print(f"  INFO {line}")
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for line in blocking[:100]:
            print(f"::error::schema drift {line}")
    return 1 if blocking else 0


if __name__ == "__main__":
    raise SystemExit(main())
