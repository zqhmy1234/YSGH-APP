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
from hbuilder_project_guard import inspect_client_project

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
# 2026-10-30 补充（一次**真实的假通过**，见 `classify_log` 头注）：结构性错误（模板/style/脚本
# 解析失败）的现实形态不止上面那几条。这两类必须单列，否则 `bad=False` + 产物旁证 ⇒ 假 ok。
FAIL_MARKERS_STRUCTURAL = (
    "[plugin:",  # uni-app x 各编译插件（uts/vue/style）的统一错误前缀
    "已停止运行",  # 编译中断的固定收尾语；失败时必现
    "Unexpected ",  # 「Unexpected token / '/' / character」等解析错
    "unknown file",  # esbuild 侧解析失败
    "ERROR in",  # webpack 风格报错头
)
# 报错定位行（`at components/X/X.uvue:1:0`）：**位置**即证据，不依赖中文/英文措辞
FAIL_LOCATION_RE = re.compile(r"at\s+\S+\.(?:uvue|uts|vue|kt|ts|js):\d+")
# HBuilderX 未启动时 cli 的拒绝语（2026-10-14 实测）：它**不编译**、打印一句就退出。
# 必须单独识别为"无法编译"——否则空日志会掉进产物旁证分支（那正是曾经假通过的入口）。
NEED_OPEN_MARKERS = ("未检测到已打开的HBuilderX", "请先执行cli open")


def classify_log(out: str) -> tuple[str, str]:
    """按编译日志文本判定结果，返回 `(verdict, reason)`。

    verdict ∈ `{"need_open", "ok", "fail", "undetermined"}`。

    ## 为什么必须抽成纯函数（2026-10-30 事故）

    `client/components/TabSearch/TabSearch.uvue` 的 style 段少闭合一个 `*/` ⇒ 注释提前结束，
    其后两行中文裸露成 CSS，其中的 `色/字号` 触发 `[plugin:uts] Unexpected '/'`，编译**确实失败**
    并打印 `已停止运行`。而旧带内逻辑只认 `FAIL_HINTS` 里的五种措辞，该错误**一种都不命中**
    ⇒ `bad=False`；`manifest.json` 每次编译必被重写 ⇒ 产物旁证为真 ⇒ 门禁报 ok、退出码 0。
    **门禁把一次真失败判成通过，就是这么发生的。**

    抽成纯函数的意义：错误文本分类可以用**真实日志片段**做负向探针（见
    `backend/tests/test_client_compile_gate.py`），而不必真的把仓库改坏去编译一次。
    """
    if any(m in out for m in NEED_OPEN_MARKERS):
        return "need_open", "HBuilderX 未启动：编译**未执行**（不是通过）"
    # ⚠️ 顺序即口径（2026-10-30 实测校准两次）：
    # ① `编译成功。` / `ready in Nms` 是 HBuilderX 的**终态裁决**，必须最先认——
    #    成功构建的日志里照样会夹带 `[plugin:uni:app-uvue-css] WARNING: ...` 与收尾的
    #    `已停止运行...`（后者是"运行会话结束"，不是失败）。把它们当失败会**假红**：
    #    本门加固后第一次复跑就撞上了这条（编译明明成功、却报 fail）。
    # ② 只有在**没有成功标记**时，那些特征才等价于失败 —— 2026-10-30 的事故日志正是
    #    「[plugin:uts] Unexpected '/' + at X.uvue:1:0 + 已停止运行」而无成功标记。
    if any(m in out for m in OK_MARKERS):
        return "ok", "日志出现成功标记"
    if any(m in out for m in FAIL_MARKERS_STRUCTURAL):
        return "fail", "日志出现结构性错误标记（[plugin:]/Unexpected/已停止运行 等，且无成功标记）"
    if any(h in out for h in FAIL_HINTS):
        return "fail", "日志出现失败措辞（编译失败/SyntaxError/Cannot find 等）"
    if FAIL_LOCATION_RE.search(out):
        return "fail", "日志出现报错定位行（at xxx.uvue:N:M）"
    return "undetermined", "日志无成功标记也无失败特征"


def find_cli() -> Path | None:
    for cand in CLI_CANDIDATES:
        p = Path(cand)
        if p.exists():
            return p
    return None


def _hbuilderx_running() -> bool:
    """HBuilderX 主进程是否在跑。探测失败**保守返回 True**（不阻断主判定）。"""
    if sys.platform != "win32":
        return True
    try:
        out = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq HBuilderX.exe", "/NH"],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        ).stdout
        return "HBuilderX.exe" in out
    except Exception:  # noqa: BLE001 —— 探测失败不影响编译主流程
        return True


def ensure_hbuilderx_open(cli: Path) -> str:
    """确保 HBuilderX 已启动；返回空串 = 就绪，否则返回**未就绪原因**。

    ## 为什么必须有这一步（2026-10-14 实测，一次真实的假通过）

    `cli launch ... --compile true` 在 HBuilderX **未启动**时只打印一句
    「未检测到已打开的HBuilderX，请先执行cli open启动HBuilderX后再重试」就立即退出，
    **什么也不编译**（实测日志仅 1 行）。而当时门禁靠"产物时间戳旁证"兜底，
    该旁证又因 `time.monotonic()` 与文件 mtime 量级不可比而**恒真** ⇒ 空日志被判
    「编译成功」。两步叠加的后果：**门禁从来没在 HBuilderX 未启动的机器上真正编译过**。
    """
    if _hbuilderx_running():
        return ""
    try:
        subprocess.run([str(cli), "open"], capture_output=True, timeout=90, check=False)
    except Exception as exc:  # noqa: BLE001 —— 拉起失败由调用方按环境错误处理
        return f"cli open 调用失败：{exc}"
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if _hbuilderx_running():
            return ""
        time.sleep(2)
    return "HBuilderX 启动超时（60s 内未检测到进程）"


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

    # 2026-10-23：`--project client` 按名称选择。HBuilderX 工作区曾有当前仓库与
    # 旧 YSGH-PR3/client 两个同名项目，CLI 静默编译旧版 102 却仍打印成功。
    # 任何编译前必须先证明唯一且路径正确；同名冲突不能靠看日志猜。
    unique_ok, unique_detail = inspect_client_project(CLIENT)
    if not unique_ok:
        return "fail", ENV_ERR_PREFIX + " HBuilderX 项目选择不安全：" + unique_detail

    # 2026-10-14：HBuilderX 未启动时 `cli launch` **不编译**（只打印一句就走），
    # 必须先把它拉起来；拉不起来就如实报环境错误，**绝不**降级成"通过"。
    not_ready = ensure_hbuilderx_open(cli)
    if not_ready:
        return (
            "skip",
            ENV_ERR_PREFIX + f" HBuilderX 未就绪（{not_ready}）：编译**未执行**（不是通过）。"
            "请手动开一次 HBuilderX（或设 HBUILDERX_CLI 指向可用实例）后重跑。",
        )

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    # ⚠️ 2026-10-14：产物时间戳旁证必须用**墙上时钟**比较。原实现把 `time.monotonic()`
    # （开机以来秒数，量级 1e5）直接与文件 `st_mtime`（Unix 纪元，量级 1.7e9）比大小，
    # `mt > started` **恒为真** ⇒ 只要日志为空就判"产物已更新 ⇒ 成功"，
    # **假通过的编译门**（2026-10-14 实测：产物实为两天前的旧件却报 ok）。
    started_wall = time.time()
    # ⚠️ 为什么用「重定向到文件」而不是 `capture_output=True`（2026-10-11 实测）：
    # HBuilderX **已启动**时，`cli launch ...` 会把命令**委派给现有实例**然后立即返回——
    # 此时子进程 stdout 只有一行 `HBuilderX Version: 5.26`，编译日志根本不出现在管道里
    # （手工 `Start-Process -RedirectStandardOutput` 却能拿到完整日志）。用文件重定向
    # 与被验证过的手工路径完全一致 ⇒ 不再依赖 cli 在管道模式下的行为。
    with LOG_PATH.open("w", encoding="utf-8", errors="replace") as sink:
        # HBuilderX 5.24 的 CLI 只接受已导入项目的名称；传绝对路径会误报
        # “不是 uni-app 项目，仅编译模式暂不支持”（本机 2026-10-23 实测）。
        proc = subprocess.Popen(
            [str(cli), "launch", "app-android", "--project", CLIENT.name, "--compile", "true"],
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
    verdict, reason = classify_log(out)

    # ⚠️ 2026-10-14：HBuilderX 未启动时 cli **不编译**，只打印下面这句并立即退出。
    # 必须先识别它——否则空日志会掉进"产物旁证"分支，而那正是曾经假通过的入口。
    if verdict == "need_open":
        return (
            "skip",
            ENV_ERR_PREFIX + f" {reason}。"
            "需先 `cli open`（本门已尝试自动拉起但未成功）后重跑。",
        )

    if verdict == "ok":
        m = re.search(r"ready in (\d+)ms", out)
        cost = f"{m.group(1)}ms" if m else f"{elapsed:.1f}s"
        return "ok", f"客户端编译成功（{cost}）；日志 {LOG_PATH.relative_to(REPO)}"

    # 双判据兜底：cli 委派给现有实例时日志可能**写在 HBuilderX 自己的日志里**而本文件为空。
    # 此时看**编译产物**是否在本次调用后更新过（编译成功必然触碰 unpackage/dist/dev）。
    # ⚠️ 时间基准必须是**墙上时钟**（见 `started_wall` 处注释：原来传 monotonic 导致恒真 ⇒ 假通过）。
    # ⚠️ 2026-10-30：只有在 `verdict == "undetermined"` 时才允许走旁证——出现任何失败特征
    # （结构化标记/失败措辞/报错定位行）一律判 fail，绝不用产物时间戳覆盖。
    touched = _artifact_touched_since(started_wall)
    if verdict == "undetermined" and touched:
        return (
            "ok",
            "未捕获到编译日志（cli 委派给运行中的 HBuilderX），但编译产物已在本次调用后更新"
            f"（{touched}）⇒ 判为成功",
        )

    if code == -1:
        return "fail", f"编译超时（>{timeout_s}s）——日志见 {LOG_PATH.relative_to(REPO)}"

    tail = "\n".join([ln for ln in out.splitlines() if ln.strip()][-12:]) or "（日志为空）"
    if verdict == "fail":
        return "fail", f"客户端编译失败（{reason}；退出码 {code}）：\n{tail}"
    return (
        "fail",
        f"编译结果**无法判定**（{reason}，产物也未更新 ⇒ 按「未验证」处理，退出码 {code}）：\n{tail}",
    )


def _artifact_touched_since(started_wall: float) -> str:
    """编译产物是否在 `started_wall` 之后被写过；返回 `"<相对路径> @ <时间>"`，否则空串。

    用途：HBuilderX 已运行时 cli 走 IPC，日志不进我们的管道 ⇒ 用"产物被重新生成"
    作为编译发生的旁证。只扫一层关键目录，避免全量遍历拖慢门禁。

    ⚠️ 2026-10-14 修（一次**真实的假通过**）：形参原先传的是 `time.monotonic()`
    （开机以来秒数，量级 1e5），而文件 `st_mtime` 是 Unix 纪元（量级 1.7e9）⇒
    `mt > started` **恒为真** ⇒ 只要日志为空就判"产物已更新 ⇒ 编译成功"。
    实测：HBuilderX 未启动（编译根本没跑、产物是两天前的旧件）时门禁仍报 ok。
    现改传 `time.time()`（墙上时钟），并回显命中文件与时间，便于人工核对。
    """
    dist = CLIENT / "unpackage" / "dist" / "dev" / "app-android"
    if not dist.exists():
        return ""
    newest = ""
    newest_mtime = started_wall
    try:
        for p in dist.rglob("*"):
            if not p.is_file():
                continue
            mt = p.stat().st_mtime
            if mt > newest_mtime:
                newest_mtime = mt
                stamp = time.strftime("%m-%d %H:%M:%S", time.localtime(mt))
                newest = f"{p.relative_to(CLIENT)} @ {stamp}"
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
