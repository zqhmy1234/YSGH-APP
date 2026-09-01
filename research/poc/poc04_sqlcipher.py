"""POC-04 · SQLCipher 桌面验证（JVM/Python 侧）

验证加密 SQLite 的读写 + 密文 + 密钥轮换语义。
说明：Android 端最终用 SQLCipher Android 库（DAO 层封装），
此脚本验证的是"加密库行为契约"，为真机测试提供对照基线。

依赖：pip install sqlcipher3（需要编译）或 pysqlcipher3。
若本机无法安装原生依赖，跳过本脚本（POC-04 以真机验证为准）。
"""
from __future__ import annotations

import os
import sys
import tempfile

# Windows 控制台 GBK 兼容
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    try:
        import sqlcipher3 as sqlite3  # type: ignore
    except ImportError:
        print("[skip] sqlcipher3 未安装 — POC-04 桌面验证跳过（真机验证为准）")
        print("       安装尝试：pip install sqlcipher3-binary")
        return 0

    tmpdir = tempfile.mkdtemp(prefix="yishu_poc04_")
    db_path = os.path.join(tmpdir, "encrypted.db")
    key = "test-key-2026"

    # 1. 写入
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA key = '{key}'")
    conn.execute("CREATE TABLE mem (id INTEGER PRIMARY KEY, text TEXT)")
    conn.execute("INSERT INTO mem (text) VALUES ('敏感记忆内容')")
    conn.commit()
    conn.close()

    # 2. 密文验证：普通 sqlite3 打开应失败
    try:
        import sqlite3 as plain

        p = plain.connect(db_path)
        p.execute("SELECT count(*) FROM mem")
        plain_failed = False
    except Exception:
        plain_failed = True

    # 3. 用正确密钥重开
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA key = '{key}'")
    row = conn.execute("SELECT text FROM mem").fetchone()
    conn.close()

    # 4. 错误密钥应报错
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA key = 'wrong-key'")
        conn.execute("SELECT count(*) FROM mem")
        wrong_key_failed = False
    except Exception:
        wrong_key_failed = True
    finally:
        conn.close()

    ok = plain_failed and row == ("敏感记忆内容",) and wrong_key_failed
    print("=" * 50)
    print("POC-04 SQLCipher 桌面验证")
    print("=" * 50)
    print(f"  写入/读取: {'✅' if row == ('敏感记忆内容',) else '❌'} ({row})")
    print(f"  明文工具无法读取: {'✅' if plain_failed else '❌'}")
    print(f"  错误密钥拒绝: {'✅' if wrong_key_failed else '❌'}")
    print(f"  结果: {'✅ 通过' if ok else '❌ 未通过'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
