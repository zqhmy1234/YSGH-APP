#!/usr/bin/env python3
"""加载 Agent 服务环境变量（去 Coze 版）。

上游实现（2026-09-23 前）：`from coze_workload_identity import Client` → 从 **Coze 平台**
拉环境变量（**根本不读 `.env`**，见 agent/UPSTREAM.md 四-1）。
本版：从 `agent/.env` 读（python-dotenv），仍是 `export` 语句形态以兼容
`eval $(python scripts/load_env.py)` 的既有调用方式（`scripts/local_run.sh`）。

用法：
  eval $(python scripts/load_env.py)     # bash/Linux
  python scripts/load_env.py             # 仅打印（调试用）
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# agent/ 根目录：本文件在 agent/scripts/ 下 → parents[1] = agent/
AGENT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = AGENT_ROOT / ".env"
ENV_EXAMPLE = AGENT_ROOT / ".env.example"


def _mask(key: str) -> str:
    """日志用：只暴露变量名，**绝不含值**。"""
    return key


def main() -> int:
    if not ENV_FILE.exists():
        print(
            f"# 缺少 {ENV_FILE} —— 请先 `cp {ENV_EXAMPLE.name} .env` 并填值"
            f"（模板：{ENV_EXAMPLE}）",
            file=sys.stderr,
        )
        return 1

    try:
        from dotenv import dotenv_values
    except ImportError:
        print("# 缺少 python-dotenv —— pip install python-dotenv", file=sys.stderr)
        return 1

    values = dotenv_values(ENV_FILE)
    exported = 0
    for key, value in values.items():
        if value is None:
            continue
        # 转义单引号，保持 `eval $(...)` 语义安全
        safe = value.replace("'", "'\\''")
        print(f"export {key}='{safe}'")
        os.environ[key] = value  # 同进程内也生效（调试直跑时）
        exported += 1

    # stderr 输出统计，不污染 stdout 的 export 语句流
    print(f"# 已从 {ENV_FILE.name} 载入 {exported} 个变量：{', '.join(_mask(k) for k in values)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
