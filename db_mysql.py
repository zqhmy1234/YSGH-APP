import os
import sqlite3

from dotenv import load_dotenv

load_dotenv()

DB_TYPE = os.getenv("DB_TYPE", "sqlite")
DB_PATH = os.getenv("DB_PATH", "photo_pipeline.db")

# 老库自动迁移时新增的列（幂等 ALTER TABLE）
PHOTO_COLS = {
    "content_hash": "TEXT",
    "thumb_path": "TEXT",
    "is_sensitive": "INTEGER DEFAULT 0",
}


def _sqlite_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _mysql_conn():
    import pymysql

    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DB", "photo_assets"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    )


def _ensure_columns(conn):
    """幂等迁移：给已存在的表补新列，不丢历史数据。"""
    if DB_TYPE == "mysql":
        cur = conn.cursor()
        cur.execute("SHOW COLUMNS FROM photo_assets")
        existing = {r[0] for r in cur.fetchall()}
        for name, ddl in PHOTO_COLS.items():
            if name not in existing:
                cur.execute(f"ALTER TABLE photo_assets ADD COLUMN {name} {ddl}")
        conn.commit()
    else:
        existing = {r[1] for r in conn.execute("PRAGMA table_info(photo_assets)")}
        for name, ddl in PHOTO_COLS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE photo_assets ADD COLUMN {name} {ddl}")
        conn.commit()


def init_db():
    """建表 photo_assets / task_log；老库自动迁移新列。"""
    if DB_TYPE == "mysql":
        conn = _mysql_conn()
        conn.cursor().execute("""
            CREATE TABLE IF NOT EXISTS photo_assets (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(64),
                file_path VARCHAR(255),
                ocr_text TEXT,
                content_hash VARCHAR(64),
                thumb_path VARCHAR(255),
                is_sensitive TINYINT DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.cursor().execute("""
            CREATE TABLE IF NOT EXISTS task_log (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                asset_hash VARCHAR(64),
                stage VARCHAR(32),
                error_type VARCHAR(64),
                message TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
        conn.commit()
        _ensure_columns(conn)
        conn.cursor().execute(
            "CREATE INDEX IF NOT EXISTS idx_photo_assets_hash ON photo_assets(content_hash)"
        )
        conn.commit()
        conn.close()
    else:
        conn = _sqlite_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS photo_assets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                file_path TEXT,
                ocr_text TEXT,
                content_hash TEXT,
                thumb_path TEXT,
                is_sensitive INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_hash TEXT,
                stage TEXT,
                error_type TEXT,
                message TEXT,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        conn.commit()
        _ensure_columns(conn)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_photo_assets_hash ON photo_assets(content_hash)"
        )
        conn.commit()
        conn.close()


def save_asset(
    user_id: str,
    file_path: str,
    ocr_text: str,
    content_hash: str = "",
    thumb_path: str = "",
    is_sensitive: int = 0,
) -> int:
    """插入一条资产记录，返回自增 id。"""
    if DB_TYPE == "mysql":
        conn = _mysql_conn()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO photo_assets
                (user_id, file_path, ocr_text, content_hash, thumb_path, is_sensitive)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (user_id, file_path, ocr_text, content_hash, thumb_path, is_sensitive),
        )
        asset_id = cur.lastrowid
        conn.commit()
        conn.close()
    else:
        conn = _sqlite_conn()
        cur = conn.execute(
            """
            INSERT INTO photo_assets
                (user_id, file_path, ocr_text, content_hash, thumb_path, is_sensitive)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (user_id, file_path, ocr_text, content_hash, thumb_path, is_sensitive),
        )
        asset_id = cur.lastrowid
        conn.commit()
        conn.close()
    return asset_id


def get_asset(asset_id: int):
    """按 id 查询，返回 dict。"""
    if DB_TYPE == "mysql":
        conn = _mysql_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM photo_assets WHERE id = %s", (asset_id,))
        row = cur.fetchone()
        conn.close()
        return row
    else:
        conn = _sqlite_conn()
        cur = conn.execute("SELECT * FROM photo_assets WHERE id = ?", (asset_id,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None


def find_asset_by_hash(content_hash: str):
    """按 SHA-256 查重，返回已有记录 dict 或 None。"""
    if not content_hash:
        return None
    if DB_TYPE == "mysql":
        conn = _mysql_conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM photo_assets WHERE content_hash = %s", (content_hash,))
        row = cur.fetchone()
        conn.close()
        return row
    else:
        conn = _sqlite_conn()
        cur = conn.execute(
            "SELECT * FROM photo_assets WHERE content_hash = ?", (content_hash,)
        )
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None


def log_task(asset_hash: str, stage: str, error_type: str, message: str):
    """记录一次失败任务（用于统计 Pipeline 成功率）。"""
    if DB_TYPE == "mysql":
        conn = _mysql_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO task_log (asset_hash, stage, error_type, message) VALUES (%s, %s, %s, %s)",
            (asset_hash, stage, error_type, message),
        )
        conn.commit()
        conn.close()
    else:
        conn = _sqlite_conn()
        conn.execute(
            "INSERT INTO task_log (asset_hash, stage, error_type, message) VALUES (?, ?, ?, ?)",
            (asset_hash, stage, error_type, message),
        )
        conn.commit()
        conn.close()
