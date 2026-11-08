"""Initialize the dedicated Agent integration-test database.

The Agent test suite includes real PostgreSQL checks for the three memory-domain
tables. Production schema ownership stays with the backend Alembic chain; this
script exists only to make an isolated test database reproducible in CI and on
a developer machine.

Safety:
- refuses database names that do not contain ``test``;
- creates only the three Agent-owned tables, not unrelated database objects;
- category seeding is idempotent.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from storage.database.shared.model import Base

TABLE_ORDER = ("memory_categories", "memories", "knowledge_collections")
CATEGORIES = ("情绪", "事件", "想法", "灵感", "人物", "地点", "待分类")


def _database_url() -> str:
    value = (os.getenv("DATABASE_URL") or "").strip()
    if not value:
        raise SystemExit("DATABASE_URL is required")
    database_name = make_url(value).database or ""
    if "test" not in database_name.lower():
        raise SystemExit(
            f"Refusing to initialize non-test database {database_name!r}; the database name must contain 'test'"
        )
    return value


def main() -> None:
    engine = create_engine(_database_url(), pool_pre_ping=True)
    with engine.begin() as connection:
        for table_name in TABLE_ORDER:
            Base.metadata.tables[table_name].create(connection, checkfirst=True)

        for category in CATEGORIES:
            connection.execute(
                text("INSERT INTO memory_categories (name) VALUES (:name) ON CONFLICT (name) DO NOTHING"),
                {"name": category},
            )

        table_count = connection.execute(
            text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name = ANY(:tables)"
            ),
            {"tables": list(TABLE_ORDER)},
        ).scalar_one()
        category_count = connection.execute(text("SELECT count(*) FROM memory_categories")).scalar_one()

    print(f"Agent test database ready: tables={table_count}/{len(TABLE_ORDER)} categories={category_count}")


if __name__ == "__main__":
    main()
