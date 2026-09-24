"""门禁工具**子进程统一包装**（D14-12 / B10-i）。

原状（审计域⑭记录）：`review_agent.run()` 与 `test_agent.run()` **各写一份** subprocess 包装，
且三处**语义分歧**——
  · 超时默认 **300 vs 600**；
  · 基础 env 差一项（`test_agent` 多注入 `PYTHONPATH=REPO`）；
  · `test_agent` 多一段「`returncode < 0` ⇒ 疑似内存不足(OOM)」的提示分支。

现收敛为**单一实现**（UTF-8 解码 / FileNotFoundError / TimeoutExpired / OOM 提示一处维护）；
各工具的自有差异改由参数表达，**调用点语义不变**：
  · 超时 → `timeout=`（`review_agent` 沿用 300 缺省；`test_agent` 显式 600）；
  · 基础 env → `base_env=`（`test_agent` 传它含 `PYTHONPATH` 的 `SUB_ENV`）；
  · OOM 提示 → 两工具**共用**（对 `review_agent` 属**诊断增强**：仅在 `returncode < 0`
    的罕见分支多出提示文案，返回码语义不变）。

返回约定（两工具原口径一致）：`(returncode, stdout+stderr)`；
`127` = 命令不存在；`124` = 超时；负值 = 进程被信号杀（附 OOM 提示）。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 输出按 UTF-8 解码（Windows 默认 GBK 会炸）
BASE_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}

DEFAULT_TIMEOUT = 300


def run(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    env: dict | None = None,
    base_env: dict | None = None,
) -> tuple[int, str]:
    """执行 `cmd` → `(returncode, stdout+stderr)`（编码/错误分支/OOM 提示单一实现）。"""
    sub_env = {**(BASE_ENV if base_env is None else base_env), **(env or {})}
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, cwd=cwd or REPO,
            timeout=timeout, encoding="utf-8", errors="replace", env=sub_env, check=False,
        )
        if proc.returncode < 0:
            return proc.returncode, (
                f"进程被杀（returncode={proc.returncode}，疑似内存不足 OOM）\n"
                "  处理：释放内存（关 HBuilderX 编译残留/其他大进程）后重跑；"
                "或 python scripts/test_agent.py --only research 分段验证"
            )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "timeout"
