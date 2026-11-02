#!/usr/bin/env python
"""Prepare the PostgreSQL role/extension, then run Alembic to head.

Fresh table-free databases bootstrap from ORM metadata in migrations/env.py.
Existing databases apply the incremental migration chain without data loss.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent
_BACKEND_DIR = (
    _SCRIPTS_DIR if (_SCRIPTS_DIR / "alembic.ini").is_file() else _REPO_ROOT / "backend"
)
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import psycopg  # noqa: E402
from app.core.config import settings  # noqa: E402
from psycopg import sql as pgsql  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import URL, make_url  # noqa: E402

INFRA_ENV_PATH = _REPO_ROOT / "infra" / "docker" / ".env"
_SMOKE_TABLES = (
    "capsules", "contents", "events", "profile_sensitive",
    "profile_l2_evidence", "finetune_jobs", "upload_tasks", "upload_chunks",
)

def _log(msg: str) -> None:
    print(msg, flush=True)



def _tolerate_console_encoding() -> None:
    """让 stdout/stderr 在中/日文 Windows 的 GBK 控制台下不再因 ✓ / ✗ 之类字符崩溃。

    CP936（GBK）编码不含 U+2713/U+2717，直接 print 会抛
    `UnicodeEncodeError: 'gbk' codec can't encode character '\\u2713'`，
    且**恰好发生在建库中途**（角色/库刚建好、表还没建）—— 新人会以为脚本坏了。
    这里把换不出去的字符降级成 `?`，保住进度与后续步骤。

    注：若终端本身是 UTF-8（`PYTHONIOENCODING=utf-8` 或 Windows Terminal 设了 UTF-8），
    本函数是空操作，符号正常显示。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, ValueError, OSError):  # 非常规流（重定向到已关闭句柄等）
            pass



def _parse_env_file(path: Path) -> dict[str, str]:
    """极简 .env 解析（仅取 KEY=VALUE，忽略注释与内联注释），失败返回空 dict。"""
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        # 去掉行尾注释（值里带 # 的情况本项目不存在，简化处理）
        out[key.strip()] = val.split("#", 1)[0].strip()
    return out



def _resolve_admin_url(cli_value: str | None) -> str:
    """超级用户连接串：--admin-url > 环境变量 > infra/docker/.env 便利兜底。"""
    if cli_value:
        return cli_value
    env_value = os.environ.get("YSG_ADMIN_DATABASE_URL")
    if env_value:
        return env_value
    infra = _parse_env_file(INFRA_ENV_PATH)
    user = infra.get("POSTGRES_USER")
    pwd = infra.get("POSTGRES_PASSWORD")
    db = infra.get("POSTGRES_DB") or "postgres"
    if user and pwd:
        _log(f"[i] 超级用户连接串取自 {INFRA_ENV_PATH}（user={user}, db={db}）")
        return f"postgresql://{user}:{pwd}@localhost:5432/{db}"
    raise SystemExit(
        "✗ 无法确定超级用户连接串。请任选其一：\n"
        "    --admin-url postgresql://<superuser>:<pw>@localhost:5432/<db>\n"
        "    export YSG_ADMIN_DATABASE_URL=postgresql://...\n"
        f"  （也尝试过读取 {INFRA_ENV_PATH}，未找到 POSTGRES_USER/POSTGRES_PASSWORD）"
    )



def _admin_dsn(url_or_str: str | URL) -> str:
    """URL（或原始连接串）→ psycopg DSN（psycopg 不认 `postgresql+psycopg://` 方言前缀）。

    ⚠️ 传参务必给 **URL 对象或原始连接串**，**绝不要传 `str(URL)`** ——
    SQLAlchemy 的 `URL.__str__()` 等于 `render_as_string(hide_password=True)`，
    密码会被渲染成字面量 `***`；再 `make_url()` 解析回来，密码就真的变成 3 个
    字符的 `***`，连接必然报 `password authentication failed`。
    2026-09-24 实测踩到：现象是「手工连得上、脚本连不上」，排查耗时很久。
    """
    url = make_url(url_or_str)
    return url.set(drivername="postgresql").render_as_string(hide_password=False)



def _connect(dsn: str, attempts: int = 5, delay: float = 1.0):
    """带重试的 autocommit 连接。

    必要性：**刚 `CREATE DATABASE` 后立刻连新库会偶发认证/连接失败**
    （2026-09-24 实测命中：`password authentication failed for user "yishu_app"`，
    而同参数的手工连接随后即成功 —— 是建库瞬间的竞态，不是密码错）。
    连接阶段失败时**没有任何语句被执行**，所以重试是安全的。
    """
    last: Exception | None = None
    masked = make_url(dsn).render_as_string(hide_password=True)
    for attempt in range(attempts):
        try:
            return psycopg.connect(dsn, autocommit=True)
        except psycopg.OperationalError as exc:
            last = exc
            if attempt < attempts - 1:
                _log(f"    … 连接未就绪 `{masked}`，{delay:.0f}s 后重试（{attempt + 1}/{attempts - 1}）")
                time.sleep(delay)
    raise SystemExit(f"✗ 连接失败 `{masked}`（已重试 {attempts} 次）：{last}")



def _run(dsn: str, query, dry_run: bool, note: str = "") -> None:
    """在 autocommit 连接上执行**单条**语句（DDL 不能进事务）。

    query 可以是 `str` 或 `psycopg.sql.Composed`。涉及标识符/字面量的一律用
    `pgsql.Identifier` / `pgsql.Literal` 组合 —— 不做字符串拼接，免注入。
    """
    # SQL composition may contain a password literal; log only the operation.
    _log(f"    SQL> {note or 'database setup operation'}")
    if dry_run:
        return
    with _connect(dsn) as conn:
        conn.execute(query)



def _db_exists(admin_dsn: str, dbname: str) -> bool:
    with _connect(admin_dsn) as conn:
        row = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()
    return row is not None



def step1_ensure_role_and_db(admin_url: str, app_url: str, reset: bool, dry_run: bool) -> None:
    """① 建角色 + 建库 + vector 扩展（须超级用户：pgvector 非 trusted 扩展）。

    `--reset` 时先断开残留连接 → DROP DATABASE → 重建，并把 public schema 的
    owner 交还应用角色。实测**不交还 owner** 时应用角色后续无法 DROP/CREATE SCHEMA，
    会报 `must be owner of schema public`（2026-09-24 实测踩到）。
    """
    app = make_url(app_url)
    role, dbname, password = app.username, app.database, app.password
    if not role or not dbname:
        raise SystemExit(f"✗ 从 DATABASE_URL 解析不出用户名/库名：{app_url}")
    if not password:
        raise SystemExit(f"✗ DATABASE_URL 未含密码，无法建角色：{app_url}")

    admin_dsn = _admin_dsn(admin_url)
    role_i, db_i = pgsql.Identifier(role), pgsql.Identifier(dbname)
    create_db = pgsql.SQL("CREATE DATABASE {d} OWNER {o}").format(d=db_i, o=role_i)

    _log(f"① 角色与库（超级用户）… role={role} db={dbname}")

    # a) 建角色（DO 块幂等；DO 体内不能传参，故用 Literal/Identifier 内联渲染）
    _run(
        admin_dsn,
        pgsql.SQL(
            "DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = {r}) "
            "THEN CREATE ROLE {i} LOGIN PASSWORD {p}; END IF; END $$;"
        ).format(r=pgsql.Literal(role), i=role_i, p=pgsql.Literal(password)),
        dry_run,
        "幂等建角色",
    )

    # b) --reset：断开残留连接 + 删库
    if reset:
        _run(
            admin_dsn,
            pgsql.SQL(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = {d} AND pid <> pg_backend_pid()"
            ).format(d=pgsql.Literal(dbname)),
            dry_run,
            "断开残留连接",
        )
        _run(admin_dsn, pgsql.SQL("DROP DATABASE IF EXISTS {d}").format(d=db_i), dry_run)

    # c) 建库（CREATE DATABASE 不能进事务/函数 → 不用 DO 块，改「先查后建」）
    if reset or dry_run:
        _run(admin_dsn, create_db, dry_run, "仅当不存在时" if dry_run else "")
    elif _db_exists(admin_dsn, dbname):
        _log("    库已存在，跳过创建")
    else:
        _run(admin_dsn, create_db, dry_run)

    # d) 目标库内：schema 归属 + vector 扩展
    #    ⚠️ 必须用**超级用户**连目标库 —— `CREATE EXTENSION` 需要超级用户
    #    （pgvector 非 trusted），用应用角色连会报 `permission denied to create extension`。
    #
    #    ⚠️ 顺序要紧：`--reset` 必须先 DROP/CREATE SCHEMA，**再**建扩展。
    #    反过来的话 `DROP SCHEMA public CASCADE` 会把刚建好的扩展一起删掉
    #    （pgvector 的对象装在 public 里），产出一个没有 vector 扩展的库
    #    —— 2026-09-24 实测踩到，由 tests/test_db_integration.py::test_vector_extension 暴露。
    admin_db_dsn = _admin_dsn(make_url(admin_url).set(database=dbname))
    if reset:
        _run(admin_db_dsn, "DROP SCHEMA public CASCADE", dry_run)
        _run(admin_db_dsn, "CREATE SCHEMA public", dry_run)
        _run(
            admin_db_dsn,
            pgsql.SQL("ALTER SCHEMA public OWNER TO {o}").format(o=role_i),
            dry_run,
            "交还 owner",
        )
    _run(admin_db_dsn, "CREATE EXTENSION IF NOT EXISTS vector", dry_run, "非 trusted，须超级用户")
    _run(admin_db_dsn, pgsql.SQL("GRANT ALL ON SCHEMA public TO {o}").format(o=role_i), dry_run)
    _log("    ✓ 角色/库/扩展就绪")



def verify() -> bool:
    """建库后冒烟：表数 + 关键表存在性。"""
    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
        ).scalar()
        missing = [
            t for t in _SMOKE_TABLES
            if not conn.execute(text("SELECT to_regclass(:t) IS NOT NULL"), {"t": f"public.{t}"}).scalar()
        ]
    _log(f"\n=== 冒烟检查 ===  public schema 共 {n} 张表")
    if missing:
        _log(f"  ✗ 关键表缺失: {', '.join(missing)}")
        return False
    _log(f"  ✓ 关键表齐全（{len(_SMOKE_TABLES)} 张抽样检查通过）")
    return True



def main() -> int:
    _tolerate_console_encoding()
    parser = argparse.ArgumentParser(description="忆述光华 · 建角色/扩展并运行 Alembic")
    parser.add_argument("--admin-url", default=None, help="超级用户连接串（建角色/库用）")
    parser.add_argument("--reset", action="store_true", help="⚠️ 先 DROP SCHEMA 清空再重建")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的动作，不改库")
    parser.add_argument("--skip-step1", action="store_true", help="跳过角色/库创建（已存在时）")
    parser.add_argument("--no-stamp", action="store_true", help="不打 alembic 版本标记")
    args = parser.parse_args()

    if args.reset or args.no_stamp:
        parser.error("B-1 起不支持 --reset / --no-stamp；现有库只允许安全增量迁移")

    app_url = settings.database_url
    safe = make_url(app_url)
    _log("=== 忆述光华 一键建库 ===")
    _log(f"  目标库: {safe.host}:{safe.port}/{safe.database}  (user={safe.username})")
    if args.dry_run:
        _log("  [dry-run] 只打印，不执行")

    if not args.skip_step1:
        step1_ensure_role_and_db(_resolve_admin_url(args.admin_url), app_url, args.reset, args.dry_run)
    else:
        _log("① 跳过（--skip-step1）")

    if args.dry_run:
        _log("\n[dry-run] 结束。去掉 --dry-run 即真正执行。")
        return 0

    from alembic.command import upgrade
    from alembic.config import Config

    alembic_config = Config(str(_BACKEND_DIR / "alembic.ini"))
    alembic_config.attributes["bootstrap_empty_on_upgrade"] = True
    upgrade(alembic_config, "head")

    return 0 if verify() else 1


if __name__ == "__main__":
    sys.exit(main())
