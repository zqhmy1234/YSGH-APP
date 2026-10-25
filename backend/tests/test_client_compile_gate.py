"""客户端编译门的假绿回归（2026-10-24 · 由一次**真实事故**沉淀）。

## 事故（实付代价：4 轮返工 + 差点提交带崩溃的代码）

当日给 `yishu-photo-watch` 加 B-12「读真实 EXIF 拍摄时刻」时，用了顶层自由函数
`export function readExifTakenMs(path): number` + `interface.uts` 同名声明，
触发 UTS 编译器（Rust 实现）overload 解析 panic：

    thread '<unnamed>' panicked at crates/uts_transforms/src/overload.rs:232:25:
    internal error: entered unreachable code: overload error Some(Atom('readExifTakenMs' type=dynamic))
    thread caused non-unwinding panic. aborting.  → 已停止运行...

**而编译门连报两次 `[ok] 编译成功`**。根因有两层：

1. panic 之前，前面已编译模块的产物**已经写入 `unpackage/dist`**，
   于是「产物时间戳旁证」分支成立（`编译成功必然触碰 dist`）；
2. 日志里既没有 `编译失败/语法错误` 等 `FAIL_HINTS`），也没有 `error:` 判定，
   于是**崩溃信号完全没被识别**。

⇒ 一次**真崩溃**被判成「成功」，而且这两次判绿之间我还改了两版返回值写法
（`as number` → `toDouble()` → `0.0`），全部崩、全部判绿 ⇒ 白改两轮。

## 修复

`client_compile_gate.judge_log_text()` 抽成**纯函数**，判定优先级写进函数规格：
**崩溃信号 > 编译诊断 `error:` > 成功标记 > 产物旁证 > 其余一律 fail**。
崩溃与 error **优先于**成功标记，因为 HBuilderX 日志是追加式的，
一次崩溃的日志里可能仍留着上一次成功的 `ready in ...ms`。

本文件锁住 9 条行为（含 4 条**负向探针**：真实崩溃日志原文 + 产物旁证成立 ⇒ 必须 fail）。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from client_compile_gate import judge_log_text  # noqa: E402

# 真实崩溃日志片段（2026-10-24 16:56 实测原文摘录，未改写）
CRASH_LOG = """16:55:51.910 HBuilderX Version: 5.26
16:55:53.762 项目 client 开始编译
当前工程15个页面，正在编译pages/shell/shell...
uts插件[yishu-background-tasks]文件未发生变化，跳过编译
16:56:17.616 thread '<unnamed>' panicked at crates/uts_transforms/src/overload.rs:232:25:
16:56:17.619 internal error: entered unreachable code: overload error Some(Atom('readExifTakenMs' type=dynamic))
16:56:17.622 thread '<unnamed>' panicked at crates/uts_transforms/src/overload.rs:232:25:
16:56:17.625 panic in a function that cannot unwind
16:56:18.122 thread caused non-unwinding panic. aborting.
16:56:18.642 已停止运行...
"""

# 更险的一种：HBuilderX 日志追加式 ⇒ 崩溃日志里**还留着上一次成功的 ready in**
CRASH_LOG_WITH_STALE_SUCCESS = (
    "16:40:00.000 项目 client 编译成功。\n16:40:00.100 ready in 25000ms.\n" + CRASH_LOG
)

# 真实类型错误诊断（2026-10-24 17:07 实测原文摘录）
_TYPE_ERRORS = """17:07:10.775 error: 参数类型不匹配：实际类型为 'Number'，预期类型为 'Long'。
17:07:10.863 at uni_modules/yishu-photo-watch/utssdk/app-android/pending_batch.uts:244:26
17:07:10.911 已停止运行...
"""


def _judge(log: str, *, touched: str = "", code: int = 0) -> str:
    status, _ = judge_log_text(
        log, elapsed=1.0, exit_code=code, timeout_s=300, artifact_touched=touched
    )
    return status


# ---------------------------------------------------------------------------
# 负向探针：这四条是本次事故的直接复现，**必须 fail**
# ---------------------------------------------------------------------------


def test_crash_log_with_touched_artifact_is_fail():
    """事故原样：崩溃日志 + 产物旁证成立 ⇒ 必须 fail（旧实现判 ok，连骗两次）。"""
    assert _judge(CRASH_LOG, touched="index.kt @ 16:56") == "fail"


def test_crash_log_with_stale_success_marker_is_fail():
    """崩溃日志里残留上次 `ready in` ⇒ 仍必须 fail（崩溃优先于成功标记）。"""
    assert _judge(CRASH_LOG_WITH_STALE_SUCCESS, touched="index.kt @ 16:56") == "fail"


def test_type_error_diag_with_touched_artifact_is_fail():
    """真实 `error:` 诊断 + 产物已更新 ⇒ 必须 fail。"""
    assert _judge(_TYPE_ERRORS, touched="index.kt @ 17:07") == "fail"


def test_type_error_diag_with_success_marker_is_fail():
    """`error:` 与 `编译成功` 共存 ⇒ 必须 fail（诊断优先于成功标记）。"""
    assert _judge("项目 client 编译成功。\nready in 100ms\n" + _TYPE_ERRORS) == "fail"


# ---------------------------------------------------------------------------
# 正向：真成功不能被误杀（防止修复把门禁改得「永远红」）
# ---------------------------------------------------------------------------


def test_real_success_is_ok():
    """真实成功日志（含 HBuilderX 固定追加的「已停止运行」）必须判 ok。"""
    log = "17:09:21.738 项目 client 编译成功。\n17:09:21.742 ready in 28731ms.\n已停止运行..."
    assert _judge(log) == "ok"


def test_artifact_evidence_still_works_when_log_empty():
    """日志只捕到版本号（cli 委派给运行中实例）时，产物旁证仍应判 ok。

    为什么保留这条：真正的成功也可能拿不到日志；把旁证整条删掉会让门禁变成
    「HBuilderX 已运行就必红」的噪声源。前提是**没有**崩溃/error 信号（见上面四条）。
    """
    assert _judge("HBuilderX Version: 5.26", touched="Bg.kt @ 17:00") == "ok"


# ---------------------------------------------------------------------------
# 其余必须一律 fail（「未验证」不默认通过）
# ---------------------------------------------------------------------------


def test_empty_log_without_artifact_is_fail():
    assert _judge("") == "fail"


def test_timeout_is_fail():
    assert _judge("partial", code=-1) == "fail"


def test_explicit_failure_marker_is_fail():
    assert _judge("编译失败：xxx", touched="x.kt", code=1) == "fail"


def test_judge_is_pure_and_ordered():
    """规格锁：崩溃/error 的优先级必须高于成功标记与产物旁证（顺序即规格）。"""
    crash_and_success = "ready in 1ms\n" + CRASH_LOG
    assert _judge(crash_and_success, touched="a.kt") == "fail"
    assert _judge(crash_and_success, touched="") == "fail"

# ══════════════════════════════════════════════════════════════════════════
# 2026-10-26 假绿 #2：**uvue 编译错误不带 `error:` 前缀**
#
# ## 事故（代价：一版坏代码被判「编译成功」并通过 pre-commit）
#
# 给画像详情页抽 `DimEvidenceList.uvue` 时写了 `export class DimEvidenceItem`
# （uvue 组件内不允许 export class）。真实日志：
#
#     02:40:58.348 D:/.../components/DimEvidenceList/DimEvidenceList.uvue
#     02:40:58.353 32 |  ...（打印出错行源码 + 插入符 ^^^）
#     02:40:58.410 <ESC>[31mat components/DimEvidenceList/DimEvidenceList.uvue:1:0<ESC>[39m
#     02:40:58.971 已停止运行...
#
# **全程没有一行 `error:`** ⇒ `_has_diag_error()` False ⇒ 掉进「产物旁证」判 ok。
# 与 2026-10-24 的 UTS panic 是**同一类不同形态**：失败信号存在，判据没认它。
#
# ## 关键陷阱（第一版判据就踩了）
#
# `.uvue:行:列` **不能单独**当判据 —— 实测**成功**日志里也有 3 处，它们是
# `[plugin:uni:app-uvue-css] WARNING` 的定位：
#     WARNING: Unsupported dynamic style property 'background'.
#     at components/TabIndex/TabIndex.uvue:53:3
# 只看格式会把正常成功判成失败（探针 ② 当场抓出）。
#
# ## 最终判据：**错误级**定位
#   ① 该 `at ...uvue:N:M` 行被 ANSI 红色（ESC[31m）包裹（错误红 / WARNING 黄）；
#   ② 或往前 3 行内出现 `ERROR`（且不含 `WARNING`）。
# ══════════════════════════════════════════════════════════════════════════

_ESC = "\x1b"

# 真实失败日志原文摘录（2026-10-26 02:40 实测，含 ANSI 颜色）
UVUE_ERROR_LOG = (
    "02:40:38.481 HBuilderX Version: 5.26\n"
    "02:40:43.188 项目 client 开始编译\n"
    "02:40:58.348 D:/GuangH-App/client/components/DimEvidenceList/DimEvidenceList.uvue\n"
    "02:40:58.353 32 | 故这里只接收已格式化好的东西\n"
    "02:40:58.410   |  ^^^^^^^^^^^^^\n"
    + _ESC + "[31mat components/DimEvidenceList/DimEvidenceList.uvue:1:0" + _ESC + "[39m\n"
    "02:40:58.971 已停止运行...\n"
)

# 真实成功日志里的 WARNING 定位（2026-10-26 02:50 实测）——绝不能判 fail
UVUE_WARNING_LOG = (
    "02:50:37.212 " + _ESC + "[33m[plugin:uni:app-uvue-css] WARNING: Unsupported dynamic style "
    "property background." + _ESC + "[39m\n"
    "02:50:37.217 at components/TabIndex/TabIndex.uvue:53:3\n"
    "02:50:37.219 51 |    height: var(--d-raw-r269, 269rpx);\n"
    "02:50:56.367 项目 client 编译成功。\n"
    "02:50:56.375 ready in 45398ms.\n"
    "02:50:56.390 已停止运行...\n"
)


def test_uvue_error_without_error_prefix_is_fail():
    """**核心回归**：uvue 错误（无 `error:` 前缀）必须判 fail，即使产物已更新。"""
    assert _judge(UVUE_ERROR_LOG, touched="manifest.json @ now") == "fail"


def test_uvue_warning_loc_is_not_fail():
    """**反向探针**：成功日志里的 WARNING `.uvue:行:列` 定位不得被判成失败。"""
    assert _judge(UVUE_WARNING_LOG, touched="manifest.json @ now") == "ok"


def test_uvue_error_beats_stale_success_marker():
    """追加式日志：错误日志里残留上一次 `ready in` ⇒ 仍必须 fail。"""
    stale = "02:30:00.000 项目 client 编译成功。\n02:30:00.100 ready in 20000ms.\n" + UVUE_ERROR_LOG
    assert _judge(stale, touched="x.kt") == "fail"


def test_ansi_color_is_the_discriminator():
    """判据核心：红色=错误 / 黄色=WARNING。"""
    red = _ESC + "[31mat a.uvue:1:0" + _ESC + "[39m\n已停止运行...\n"
    assert _judge(red) == "fail"
    yellow = (
        _ESC + "[33m[plugin:x] WARNING: y" + _ESC + "[39m\n"
        "at a.uvue:1:0\n项目 client 编译成功。\n"
    )
    assert _judge(yellow) == "ok"
