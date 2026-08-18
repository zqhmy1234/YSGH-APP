import os
import sqlite3

from dotenv import load_dotenv

load_dotenv()

DB_TYPE = os.getenv("DB_TYPE", "sqlite")
DB_PATH = os.getenv("DB_PATH", "photo_pipeline.db")


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


def init_db():
    """建表 photo_assets。SQLite 与 MySQL 各有一套建表语句。"""
    if DB_TYPE == "mysql":
        conn = _mysql_conn()
        conn.cursor().execute("""
            CREATE TABLE IF NOT EXISTS photo_assets (
                id BIGINT AUTO_INCREMENT PRIMARY KEY,
                user_id VARCHAR(64),
                file_path VARCHAR(255),
                ocr_text TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """)
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
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        conn.commit()
        conn.close()


def save_asset(user_id: str, file_path: str, ocr_text: str) -> int:
    """插入一条资产记录，返回自增 id。"""
    if DB_TYPE == "mysql":
        conn = _mysql_conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO photo_assets (user_id, file_path, ocr_text) VALUES (%s, %s, %s)",
            (user_id, file_path, ocr_text),
        )
        asset_id = cur.lastrowid
        conn.commit()
        conn.close()
    else:
        conn = _sqlite_conn()
        cur = conn.execute(
            "INSERT INTO photo_assets (user_id, file_path, ocr_text) VALUES (?, ?, ?)",
            (user_id, file_path, ocr_text),
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
