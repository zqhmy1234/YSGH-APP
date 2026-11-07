"""门禁/脚本共享的 I/O 兜底（**唯一实现** · D14-11 · 2026-09-24 重构波 B10-n）。

为什么抽出来：把 stdout/stderr 切到 UTF-8（不可编码字符降级为 `?`）这 2~4 行，
此前在 `scripts/*.py` 里**抄了 20 处 / 15 个文件**（实测 grep），属典型"同语义多实现"：
想加一层兜底（例如顺带设 `PYTHONIOENCODING`）就得改 15 处，且**必然漏改**。

语义（与原各处**逐字等价**，且是它们的安全超集）：
  · 同时处理 stdout 与 stderr；
  · 无 `reconfigure` 的老环境**静默降级为不动**——用 `contextlib.suppress` 而非
    `try/except/pass`，以满足 ruff S110。

用法（脚本顶部，替换原来的 hasattr/reconfigure 样板）：
    import gate_io
    gate_io.force_utf8()
"""
from __future__ import annotations

import contextlib
import sys


def force_utf8() -> None:
    """把 stdout/stderr 切到 UTF-8（`errors="replace"`），任何环境都不会因此抛错。"""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
