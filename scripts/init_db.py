#!/usr/bin/env python
"""忆述光华 · 一键建库（B-1 配套）

【为什么需要这个脚本】
`alembic upgrade head` **从空库跑不通** —— baseline 迁移 `431bcaa8bd54_baseline_from_orm`
**非自包含**：它的 `upgrade()` 只做 ALTER（drop_constraint / alter_column / 建索引），
全部 68 处 `create_table` 都写在 `downgrade()` 里。它假设表**已经存在**。

项目自己的脚本就承认了这点（原文见 scripts/check_schema_drift.py）：
    :187  B 侧：先尝试 `alembic upgrade head`；失败（基线非自包含）回退 ORM metadata。
    :211  alembic upgrade head 从空库失败（基线迁移 431bcaa8bd54 非自包含，…

叠在 `schema.sql` 之上也不行（实测 `DuplicateColumn: client_event_id already exists`）——
`schema.sql` 是**中间态快照**（有 `client_event_id` 却缺 `capsules`），与迁移链双向都不可叠加。

【本脚本做的 5 步】—— 2026-09-24 实测跑通的配方，现固化成一条命令
  ① 超级用户建角色 + 建库 + vector 扩展（等价 scripts/setup_pg.sql）
  ② ORM `Base.metadata.create_all`              → 全部 ORM 表（列定义权威）
  ③ 从 `backend/sql/schema.sql` 抽**裸 SQL 表**  → 补 profile_l2_evidence 等
     （清单见下方 NON_ORM_TABLES —— 只列"有代码用但没有 ORM 模型"的表）
  ④ 最后两条**幂等**迁移的 SQL                    → capsules 索引 + event_edit_log 序列自愈
  ⑤ `alembic stamp head`                        → 版本标记对齐（head 动态取，不写死）

  ⚠️ 本文件**刻意不写死表数与 head 值** —— 它们随 ORM/迁移演进（曾因写死 25/14/b8c9d0e1f2a3
     而在三次代码推进后全部过期）。实际数字由脚本运行时打印：结尾的「冒烟检查」会报表数。

【用法】
    cd backend && python ../scripts/init_db.py                  # 建库（幂等，已有则跳过）
    python scripts/init_db.py --dry-run                         # 只打印将执行的动作
    python scripts/init_db.py --reset                           # ⚠️ 先 DROP SCHEMA 再重建（清空数据）
    python scripts/init_db.py --admin-url postgresql://ysg:pw@localhost:5432/ysg

【连接串从哪来】
  · 应用库（要建的）：读 `backend/.env` 的 `DATABASE_URL`（与 alembic 同一来源，保证一致）
  · 超级用户（建角色/库用）：优先 `--admin-url` → 环境变量 `YSG_ADMIN_DATABASE_URL`
    → 自动读工作区的 `infra/docker/.env`（仅便利，非必需）

【B-1 修好后】本脚本可退化为薄封装 —— 届时让 baseline 自包含，直接 `alembic upgrade head` 即可。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from pathlib import Path

# ---- 路径与 sys.path（必须先做，才能 import app.*）----
_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent                    # YSGH-APP/
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

import app.db.models  # noqa: E402,F401  —— 注册全部 ORM 模型到 Base.metadata
import psycopg  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.db.session import Base  # noqa: E402
from psycopg import sql as pgsql  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import URL, make_url  # noqa: E402

SCHEMA_SQL_PATH = _BACKEND_DIR / "sql" / "schema.sql"
# 工作区级的 infra 凭据（仅便利兜底；仓库不应依赖它存在）
INFRA_ENV_PATH = _REPO_ROOT.parent / "infra" / "docker" / ".env"

# 有代码在用、但**没有 ORM 模型**的表 —— 代码用裸 SQL 读写它们，所以
# `Base.metadata.create_all` 建不出来（典型：profile_l2_evidence，见
# backend/app/services/profile_annotator.py:278 的 INSERT）。
# 这 10 张从 schema.sql 抽取 DDL 单独建。
#
# ⚠️ 刻意**不含** app_settings / question_history / question_templates 三张 ——
#   它们虽在 schema.sql 里有 DDL，但 (a) backend/app 里零引用、(b) 同时出现在
#   baseline 迁移的 _LEGACY_EMPTY_TABLES 待删清单里。建了是死表，会让库表数与
#   迁移链目标态不一致。
#
# ⚠️ `user_wechat_bindings` 已于 2026-09-25 补上 ORM 模型（db/models/wechat.py:67）
#   → **必须从本清单移除**，否则 `create_all` 已建过它、这里的 `CREATE TABLE`
#   （schema.sql 里没有 IF NOT EXISTS）必然报 already exists。更糟的是：在 2026-09-28
#   修复 SAVEPOINT 之前，这一条失败会把整个事务毒化，导致其余 15 条**全部静默失效**。
#   → 教训：本清单必须与 ORM 保持同步；新增 ORM 模型后要回来删对应条目。
NON_ORM_TABLES: tuple[str, ...] = (
    "ai_request_logs", "api_cost_stats", "audit_log",
    "content_tags", "event_tags", "finetune_jobs", "guardrail_logs",
    "profile_l2_evidence", "tags", "voice_segments",
)

# 迁移链里最后两条 —— 写成了**幂等自愈**（CREATE ... IF NOT EXISTS + 条件 setval），
# 所以可以脱离 alembic 直接执行，不会重复改坏。
IDEMPOTENT_MIGRATIONS_SQL: tuple[str, ...] = (
    # b8c9d0e1f2a3_index_capsule_scan
    "CREATE INDEX IF NOT EXISTS idx_capsules_sealed_openat "
    "ON capsules (status, open_at) WHERE status = 'sealed'",
    # c8d9e0f1a2b3_guard_event_edit_log_seq
    "CREATE SEQUENCE IF NOT EXISTS event_edit_log_id_seq",
    "ALTER SEQUENCE event_edit_log_id_seq OWNED BY event_edit_log.id",
    "ALTER TABLE event_edit_log "
    "ALTER COLUMN id SET DEFAULT nextval('event_edit_log_id_seq'::regclass)",
)

# 建库后的冒烟检查表（必须存在，否则后端相关功能必炸）
_SMOKE_TABLES = (
    "capsules", "contents", "events", "profile_sensitive",
    "profile_l2_evidence", "upload_tasks", "upload_chunks",
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
    shown = str(query)
    shown = shown[:110] + ("…" if len(shown) > 110 else "")
    _log(f"    SQL> {shown}" + (f"   # {note}" if note else ""))
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


def _strip_sql_comments(chunk: str) -> str:
    """剥掉整行注释（`-- ...`）。

    必要性：按 `;` 切块后，块首常跟着章节注释（`-- ===== N. 某某域 =====`）。
    若直接 `chunk.startswith("--")` 就跳过整块，会**连块里的 CREATE TABLE 一起漏掉**
    —— 2026-09-24 实测因此漏了 app_settings / question_history / question_templates 三张。
    """
    return "\n".join(
        line for line in chunk.splitlines() if not line.strip().startswith("--")
    ).strip()


def _extract_ddl(sql_text: str, wanted: tuple[str, ...]) -> list[tuple[str, str, str]]:
    """从 schema.sql 抽指定表的 DDL，返回 `[(表名, kind, SQL)]`，kind ∈ {TABLE, INDEX}。

    按 `;` 粗切（该文件无存储过程/函数体，安全），**先剥注释再匹配**（见
    `_strip_sql_comments` 说明）。
    """
    found: list[tuple[str, str, str]] = []
    for raw in sql_text.split(";"):
        stmt = _strip_sql_comments(raw)
        if not stmt:
            continue
        m = re.search(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?(\w+)\"?", stmt, re.I)
        if m:
            if m.group(1) in wanted:
                found.append((m.group(1), "TABLE", stmt))
            continue
        m = re.search(
            r"CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?\"?\w+\"?\s+ON\s+\"?(\w+)\"?",
            stmt, re.I,
        )
        if m and m.group(1) in wanted:
            found.append((m.group(1), "INDEX", stmt))
    return found


def step2_orm_tables(dry_run: bool) -> int:
    """② ORM metadata 建表（25 张，列定义权威；checkfirst 幂等）。"""
    _log(f"② ORM 建表 … 待建 {len(Base.metadata.tables)} 张（已存在的跳过）")
    if dry_run:
        return len(Base.metadata.tables)
    engine = create_engine(settings.database_url)
    Base.metadata.create_all(engine, checkfirst=True)
    return len(Base.metadata.tables)


def step3_non_orm_tables(dry_run: bool) -> tuple[int, int]:
    """③ 补裸 SQL 表（有代码用、无 ORM 模型）。返回 (成功, 跳过)。"""
    _log(f"③ 补裸 SQL 表 … 目标 {len(NON_ORM_TABLES)} 张")
    if not SCHEMA_SQL_PATH.is_file():
        _log(f"    ⚠️ 找不到 {SCHEMA_SQL_PATH}，跳过本步（相关功能会缺表）")
        return (0, 0)

    stmts = _extract_ddl(SCHEMA_SQL_PATH.read_text(encoding="utf-8"), NON_ORM_TABLES)
    got = {name for name, kind, _ in stmts if kind == "TABLE"}
    missing = [t for t in NON_ORM_TABLES if t not in got]
    _log(f"    从 schema.sql 抽到 {len(stmts)} 条 DDL（{len(got)} 张表 + {len(stmts) - len(got)} 条索引）")
    if missing:
        # 抽不到要么是 schema.sql 改名/重排，要么又踩了「注释块误跳过」——显式报出来，别静默
        _log(f"    ⚠️ 有 {len(missing)} 张未在 schema.sql 找到: {', '.join(missing)}")

    if dry_run:
        for name, kind, _ in stmts:
            _log(f"    would create [{kind}] {name}")
        return (len(stmts), 0)

    ok = benign = skipped = 0
    fatal: list[str] = []
    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        for name, kind, stmt in stmts:
            try:
                # ⚠️ 每条语句必须各自一个 SAVEPOINT：否则单条失败会把**整个事务**标记为
                # aborted，后续每条都报 `InFailedSqlTransaction` —— 那不是"跳过一条"，
                # 而是"整批静默失效"（2026-09-28 实测：16 条全废、库只剩 31 张表）。
                with conn.begin_nested():
                    conn.execute(text(stmt))
                ok += 1
            except Exception as exc:  # noqa: BLE001 —— 单条失败不中断整批（与项目其他脚本一致）
                msg = str(exc)
                if "already exists" in msg:
                    benign += 1  # 重跑本脚本时的正常情况（DDL 没有 IF NOT EXISTS）
                    continue
                skipped += 1
                if kind == "TABLE":
                    fatal.append(f"{name}: {msg[:90]}")
                _log(f"    [失败] [{kind}] {name}: {msg[:90]}")

    if fatal:
        # 表建不出来就是库不完整，必须**响亮地失败**，不能靠后面的冒烟检查兜（它只抽样 7 张）
        raise SystemExit(
            f"✗ 有 {len(fatal)} 张表创建失败，库不完整：\n    " + "\n    ".join(fatal)
            + "\n  → 若报「already exists」之外的原因，多半是 ORM 模型与 schema.sql 已经分叉："
              "该表若已有 ORM 模型，请从 NON_ORM_TABLES 里移除。"
        )
    return (ok + benign, skipped)


def step4_idempotent_migrations(dry_run: bool) -> None:
    """④ 最后两条幂等迁移的 SQL（capsules 索引 + event_edit_log 序列自愈）。"""
    _log(f"④ 幂等迁移 SQL … {len(IDEMPOTENT_MIGRATIONS_SQL)} 条")
    if dry_run:
        return
    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        for stmt in IDEMPOTENT_MIGRATIONS_SQL:
            conn.execute(text(stmt))
    _log("    ✓ 完成")


def step5_stamp_head(dry_run: bool) -> str:
    """⑤ alembic stamp head —— 只打版本标记，不执行迁移（迁移链跑不通，见模块头注释）。"""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(_BACKEND_DIR / "alembic.ini"))
    if dry_run:
        _log("⑤ alembic stamp head（dry-run 跳过）")
        return "?"
    command.stamp(cfg, "head")
    # 回读实际版本号
    engine = create_engine(settings.database_url)
    with engine.begin() as conn:
        rev = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
    _log(f"⑤ alembic stamp head → {rev}")
    return str(rev)


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
    parser = argparse.ArgumentParser(
        description="忆述光华 · 一键建库（替代跑不通的 `alembic upgrade head`）"
    )
    parser.add_argument("--admin-url", default=None, help="超级用户连接串（建角色/库用）")
    parser.add_argument("--reset", action="store_true", help="⚠️ 先 DROP SCHEMA 清空再重建")
    parser.add_argument("--dry-run", action="store_true", help="只打印将执行的动作，不改库")
    parser.add_argument("--skip-step1", action="store_true", help="跳过角色/库创建（已存在时）")
    parser.add_argument("--no-stamp", action="store_true", help="不打 alembic 版本标记")
    args = parser.parse_args()

    app_url = settings.database_url
    safe = make_url(app_url)
    _log("=== 忆述光华 一键建库 ===")
    _log(f"  目标库: {safe.host}:{safe.port}/{safe.database}  (user={safe.username})")
    if args.reset:
        _log("  ⚠️ --reset：将清空该库的 public schema")
    if args.dry_run:
        _log("  [dry-run] 只打印，不执行")

    if not args.skip_step1:
        step1_ensure_role_and_db(_resolve_admin_url(args.admin_url), app_url, args.reset, args.dry_run)
    else:
        _log("① 跳过（--skip-step1）")

    step2_orm_tables(args.dry_run)
    ok, skipped = step3_non_orm_tables(args.dry_run)
    _log(f"    ✓ 裸 SQL 表：成功 {ok} / 跳过 {skipped}")
    step4_idempotent_migrations(args.dry_run)

    if args.no_stamp:
        _log("⑤ 跳过（--no-stamp）")
    else:
        step5_stamp_head(args.dry_run)

    if args.dry_run:
        _log("\n[dry-run] 结束。去掉 --dry-run 即真正执行。")
        return 0

    return 0 if verify() else 1


if __name__ == "__main__":
    sys.exit(main())
