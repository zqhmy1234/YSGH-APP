"""客户端编译门（uni-app x · 2026-10-11）

## 为什么需要它

`review_agent.py` 的静态轴（client 导入 / 类成员 / 死路由 / 令牌字面量）都是**启发式**，
而 `client/` 是 uni-app x 的 uvue/UTS 代码，**真正的编译期错误**（语法、模板、类型、
平台 API 误用）此前**零自动化**——只能靠人记得在 HBuilderX 里手动"运行到手机"
（实测 20~56s）。2026-10-11 复盘（重构后"新包各页面不可用"）暴露了这一盲区：
一个 UTS 语法/类型问题可以静默进入提交，直到人工编译才发现。

## 口径（对齐 AGENTS.md 的分层门禁决定）

| 调用方 | 缺 HBuilderX 时 | 编译失败时 |
|---|---|---|
| `review_agent.py --full` | **降级放行**（WARN，退出码 0；CI/无 GUI 机器不该因此恒红） | **阻断**（退出码 1/2） |
| `deploy/scripts/preflight_pack.ps1`（出包前置门） | **阻断**（出包机必须能编译） | **阻断** |

`--strict` 开关表示"缺工具即环境错误"（退出码 2），供出包门使用。

## 用法

    python scripts/client_compile_gate.py            # 人类可读输出
    python scripts/client_compile_gate.py --json     # 机读（供门禁/CI）
    python scripts/client_compile_gate.py --strict   # 缺 HBuilderX 即 env 错误

退出码：0 = 编译成功（或非严格模式下缺工具而降级放行）；1 = 编译失败；2 = 缺工具（仅 --strict）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

from gate_io import force_utf8  # noqa: E402  —— UTF-8 兜底唯一实现（同仓约定）

force_utf8()

REPO = Path(__file__).resolve().parent.parent
CLIENT = REPO / "client"
LOG_PATH = REPO / ".cowork-temp" / "client-compile-gate.log"
TIMEOUT_S = 300

ENV_ERR_PREFIX = "[环境错误]"

# HBuilderX CLI 候选位置（可用 HBUILDERX_CLI 环境变量覆盖）
CLI_CANDIDATES = tuple(
    p for p in (
        os.environ.get("HBUILDERX_CLI"),
        r"D:\HBuilderX\cli.exe",
        r"C:\HBuilderX\cli.exe",
        r"D:\HBuilderX\HBuilderX\cli.exe",
    ) if p
)

OK_MARKERS = ("编译成功", "ready in")
FAIL_HINTS = ("编译失败", "语法错误", "SyntaxError", "Unexpected token", "未找到", "Cannot find")


def find_cli() -> Path | None:
    for cand in CLI_CANDIDATES:
        p = Path(cand)
        if p.exists():
            return p
    return None


def compile_client(timeout_s: int = TIMEOUT_S) -> tuple[str, str]:
    """执行编译门。

    @return (status, detail)
      status ∈ {"ok", "fail", "skip"}
      · ok   —— 日志出现"编译成功/ready in"
      · fail —— 日志出现失败特征（或进程超时/非 0 退出且无成功标记）
      · skip —— 未找到 HBuilderX CLI（detail 以 [环境错误] 开头）
    """
    if not CLIENT.exists():
        return "fail", f"client/ 目录不存在：{CLIENT}"

    cli = find_cli()
    if cli is None:
        return (
            "skip",
            ENV_ERR_PREFIX + " 未找到 HBuilderX CLI（cli.exe）。"
            "设置环境变量 HBUILDERX_CLI 指向它，或在有 HBuilderX 的机器上跑。",
        )

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    # ⚠️ 为什么用「重定向到文件」而不是 `capture_output=True`（2026-10-11 实测）：
    # HBuilderX **已启动**时，`cli launch ...` 会把命令**委派给现有实例**然后立即返回——
    # 此时子进程 stdout 只有一行 `HBuilderX Version: 5.26`，编译日志根本不出现在管道里
    # （手工 `Start-Process -RedirectStandardOutput` 却能拿到完整日志）。用文件重定向
    # 与被验证过的手工路径完全一致 ⇒ 不再依赖 cli 在管道模式下的行为。
    with LOG_PATH.open("w", encoding="utf-8", errors="replace") as sink:
        proc = subprocess.Popen(
            [str(cli), "launch", "app-android", "--project", str(CLIENT), "--compile", "true"],
            stdout=sink,
            stderr=subprocess.STDOUT,
        )
        try:
            code = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = -1

    out = LOG_PATH.read_text(encoding="utf-8", errors="replace") if LOG_PATH.exists() else ""
    elapsed = time.monotonic() - started

    ok = any(m in out for m in OK_MARKERS)
    bad = any(h in out for h in FAIL_HINTS)

    if ok:
        m = re.search(r"ready in (\d+)ms", out)
        cost = f"{m.group(1)}ms" if m else f"{elapsed:.1f}s"
        return "ok", f"客户端编译成功（{cost}）；日志 {LOG_PATH.relative_to(REPO)}"

    # 双判据兜底：cli 委派给现有实例时日志可能**写在 HBuilderX 自己的日志里**而本文件为空。
    # 此时看**编译产物**是否在本次调用后更新过（编译成功必然触碰 unpackage/dist/dev）。
    touched = _artifact_touched_since(started)
    if touched and not bad:
        return (
            "ok",
            f"未捕获到编译日志（cli 委派给运行中的 HBuilderX），但编译产物已在本次调用后更新（{touched}）⇒ 判为成功",
        )

    if code == -1:
        return "fail", f"编译超时（>{timeout_s}s）——日志见 {LOG_PATH.relative_to(REPO)}"

    tail = "\n".join([ln for ln in out.splitlines() if ln.strip()][-12:]) or "（日志为空）"
    if bad:
        return "fail", f"客户端编译失败（退出码 {code}）：\n{tail}"
    return "fail", f"编译结果无法判定（无成功标记，退出码 {code}）：\n{tail}"


def _artifact_touched_since(started: float) -> str:
    """编译产物是否在 `started` 之后被写过（返回最早被写文件的相对路径，否则空串）。

    用途：HBuilderX 已运行时 cli 走 IPC，日志不进我们的管道 ⇒ 用"产物被重新生成"
    作为编译发生的旁证。只扫一层关键目录，避免全量遍历拖慢门禁。
    """
    dist = CLIENT / "unpackage" / "dist" / "dev" / "app-android"
    if not dist.exists():
        return ""
    newest = ""
    newest_mtime = started
    try:
        for p in dist.rglob("*"):
            if not p.is_file():
                continue
            mt = p.stat().st_mtime
            if mt > newest_mtime:
                newest_mtime = mt
                newest = str(p.relative_to(CLIENT))
    except Exception:  # noqa: BLE001 —— 旁证失败不影响主判定
        return ""
    return newest


def main() -> int:
    ap = argparse.ArgumentParser(description="uni-app x 客户端编译门")
    ap.add_argument("--json", action="store_true", help="输出 JSON（CI/门禁用）")
    ap.add_argument("--strict", action="store_true", help="缺 HBuilderX 时按环境错误处理（退出码 2）")
    ap.add_argument("--timeout", type=int, default=TIMEOUT_S, help=f"超时秒数（默认 {TIMEOUT_S}）")
    args = ap.parse_args()

    status, detail = compile_client(timeout_s=args.timeout)

    if args.json:
        print(json.dumps({"status": status, "detail": detail}, ensure_ascii=False))
    else:
        print(f"[{status}] {detail}")

    if status == "ok":
        return 0
    if status == "skip":
        # 非严格：降级放行（0）；严格：环境错误（2）
        return 2 if args.strict else 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
