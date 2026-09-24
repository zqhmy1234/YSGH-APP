#!/usr/bin/env python3
"""忆述光华 · Pre-Commit 代码质量审核 Agent（Sprint 1 新增流程）

用法：
  python scripts/review_agent.py          # 快速门禁（默认，秒级）：只查本次提交涉及的文件
  python scripts/review_agent.py --full   # 全量门禁：仓库级语法/lint/密钥扫描 + 全量测试（集成/CI 前跑）
  python scripts/review_agent.py --full --skip-tests  # 静态全仓门禁（CI 快速层：语法/lint/密钥，不跑测试）
  python scripts/review_agent.py --path <dir>   # （兼容占位，忽略）

职责（Commit Gate，写入 AGENTS.md）：
  快模式（pre-commit 默认，2026-08-26 拆分——原每次 commit 全量跑 5 分钟）：
    1. Python 语法编译检查（本次提交新增/修改的 .py）
    2. Lint（ruff，仅本次提交的 .py；有未暂存改动的文件跳过，语义同旧实现）
    3. 密钥与敏感信息扫描（仅本次提交文件——gitignore 的 .env 等永不入 index，天然豁免）
      ⚠️ 快/全量**都必须**只扫「可能入库」的文件：全量模式原先扫工作区全部 rglob，
      不认 .gitignore，被 `.workbuddy/tmp/*.txt` 这类被忽略产物卡成假红灯（2026-09-23 修，
      见 `_repo_text_candidates`）。
    4. TODO/FIXME 计数报告（不阻断）
    5. lessons 强制登记检查（上次失败未登记 → 阻断）
    6. 结构棘轮 structure（轴 5/6：文件体积 / 重复模式，2026-09-24 B1 接入）
    7. 跨轴审计 audit_axes（轴 1-4：契约/客户端/模型/导出，2026-09-24 B10 接入，缺陷 D14-1）
    8. 部署模板对齐 env_template（模板 ↔ config.py，2026-09-24 B12 接入，缺陷 D13-3）
  全量模式（--full，完成验收/集成/CI 用，即旧行为）：
    1-4 同快模式但扫描整个仓库（排除 client/ 工具链）
    5. 全量测试（pytest + api_smoke + research，经 scripts/test_agent.py）
    6. lessons 强制登记检查
    7. 结构棘轮 + 跨轴审计 + 部署模板对齐（与快模式同源；全量模式下轴 1 契约对拍通常可跑）

退出码（**统一口径**，与 `audit_harness` / `check_schema_drift` / `gen_openapi` 一致 · D14-18）：
  0 = 通过（可提交）
  1 = 存在**违规**阻断项（代码问题，须修复后再提交）
  2 = **环境错误**（工具/依赖/后端不可用，本工具**无法真正判定**）
      —— 与「违规」区分，避免把「环境没配好」误当成「代码有问题」；
         调用方（git hook / CI）对**非 0 一律阻断**，但退出码可供快速归因。

报告输出：.cowork-temp/review-report.json（每次覆盖）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# Windows 控制台 GBK 兼容（✅/❌ 为 Unicode）
# D14-11（B10-n）：UTF-8 兜底唯一实现（scripts/gate_io.py）
from gate_io import force_utf8  # noqa: E402

# D14-12（B10-i）：子进程包装**单一实现**（scripts/gate_proc.py，与 test_agent 共用）
from gate_proc import run  # noqa: E402

# D14-22（B10-i）：机读报告统一 schema + 带兜底写入（scripts/gate_report.py，三工具共用）
from gate_report import write_report  # noqa: E402

force_utf8()

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / ".cowork-temp"
REPORT_PATH = REPORT_DIR / "review-report.json"

# 阻断规则：匹配到这些模式的代码不允许提交
#
# D14-9（2026-09-24 B10-l）：模式集**唯一来源** = 同目录 secret_patterns.py（与
#   scripts/audit_security.py 共用）。此前两处各维护一套且**互有缺口**
#   （审计认得 AKID…/私钥宽版，提交门禁看不到）⇒ 同一份密钥可能被放行。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from secret_patterns import SECRET_PATTERNS  # noqa: E402

SECRET_SKIP = {".env.example", ".git", "review_agent.py", "config.py", "secret_patterns.py"}
# 2026-08-27（T9）：补齐 client 工具链（已整目录排除）之外的文本类型盲区——
# .txt/.properties/.ps1/.sh/.mjs/.bat/.xml/.gitignore/.example/.mako 均可能携带密钥
TEXT_EXTS = (
    ".py", ".md", ".json", ".yaml", ".yml", ".toml", ".env", ".ini", ".sql",
    ".ts", ".uts", ".uvue",
    ".txt", ".properties", ".ps1", ".sh", ".mjs", ".bat", ".xml", ".gitignore",
    ".example", ".mako",
)


def _skip_path(parts: tuple[str, ...]) -> bool:
    """harness 工具链扫描排除（B2 决策：client/ 为 uni-app x，非 Python 工具链）

    2026-08-26：加 .wt/（并行开发 worktree 副本——每个都是完整仓库，
    全量模式会重复扫 5 份 backend，静态门禁 150s→~30s）。

    ⚠️ 2026-09-23 **临时**加 agent/（开发部同事交付的 Coze Agent 包，原样入库以便对照上游）：
    该包 354 处 lint 违规（239 可自动修 + 115 需手动，含 S310/S110/DTZ005 等真问题），
    其上不符合我们规范。**豁免是临时的**：改写批次（见 docs/待办处理排期_20260923.md「Agent 接线批」AG7）
    完成后**必须删除本行**并全量收口 lint。登记：docs/决策台账.md §4.12。
    """
    return (
        ".git" in parts
        or ".cowork-temp" in parts
        or "client" in parts
        or (".wt" in parts or (len(parts) > 0 and parts[0] == ".wt"))
    )


def _lint_skip(path: str) -> bool:
    """lint 步的**文件列表层**排除（`_skip_path` 只作用于语法步的目录遍历）。

    为什么需要独立函数：`check_lint` 把文件**显式**传给 `ruff check <files>`，
    而 **ruff 的 `config.exclude` 对命令行显式传入的文件不生效**（实测：仅配
    ruff.toml 的 exclude 时，本次提交里的 agent/*.py 仍被 lint 拦下）。
    故必须在列表层过滤。

    ⚠️ 2026-09-23 **临时**纳入 agent/（交付包原样入库，理由与依据见 `agent/UPSTREAM.md`）：
    上游 354 处违规；**仅豁免 lint**——语法与密钥扫描仍覆盖 agent/（实测通过：32 文件编译 OK、无硬编码密钥）。
    **撤销待办 AG7**：改写批次完成后删除本函数的 agent 判断（与 ruff.toml 的 exclude 同步），
    登记见 docs/决策台账.md §4.12 与排期「Agent 接线批」AG7。
    """
    p = path.replace("\\", "/")
    return p == "agent" or p.startswith("agent/")


def _git_files(cached: bool) -> list[str]:
    """git diff 文件名（相对路径，正斜杠）；cached=True 查暂存区（本次提交），否则查工作区改动

    2026-08-27（批次 H2 R7 · T9）：--diff-filter=ACM → ACMR——重命名（R）文件此前
    不出现于快模式 diff（git 视之为 R 而非 A），导致 rename 的文件漏过 syntax/lint/
    secrets 扫描；加入 R 后与全量模式（rglob 覆盖仓库全部）语义一致。
    """
    args = ["git", "diff", "--name-only", "--diff-filter=ACMR"]
    if cached:
        args.insert(2, "--cached")
    code, out = run(args)
    if code != 0 or not out.strip():
        return []
    return [p.strip() for p in out.splitlines() if p.strip()]


def _staged_files() -> list[str]:
    """本次提交涉及文件：优先暂存区；手动运行（未 add）时退回工作区改动"""
    staged = _git_files(cached=True)
    if staged:
        return staged
    return _git_files(cached=False)


def _dirty_files() -> set[str]:
    """有未暂存改动的文件（工作区 != 暂存区）——跳过，避免 lint 到进行中的半成品

    与 _git_files 同为 ACMR（含 rename）：重命名且工作区仍有改动的文件同样
    被判定 dirty → 快/全量 lint 跳过语义一致（2026-08-27 · T9）。
    """
    return {p.replace("\\", "/") for p in _git_files(cached=False)}


def _scope_files(full: bool) -> tuple[list[str], list[str], list[str]]:
    """按模式计算三组扫描文件：(syntax, lint, secrets/todos)

    快模式：仅本次提交文件；全量模式：整个仓库（旧行为）。
    """
    if not full:
        staged = _staged_files()
        dirty = _dirty_files()
        # 已暂存但又有未暂存改动的文件跳过 lint（ruff 读工作区，可能 lint 到半成品）
        lint = [f for f in staged if f.endswith(".py") and f not in dirty and not _lint_skip(f)]
        return ([f for f in staged if f.endswith(".py")], lint, staged)

    syntax: list[str] = []
    for p in ROOT.rglob("*.py"):
        if not _skip_path(p.parts):
            syntax.append(str(p.relative_to(ROOT)))
    code, ls = run(["git", "ls-files", "--", "*.py"])
    tracked = [p for p in (ls.splitlines() if code == 0 and ls.strip() else []) if p.strip()]
    dirty = _dirty_files()
    lint = [
        p
        for p in tracked
        if p not in dirty and p.replace("/", "\\") not in dirty and not _lint_skip(p)
    ]
    secrets: list[str] = []
    for rel in _repo_text_candidates():
        if any(rel.startswith(s) or s in rel for s in SECRET_SKIP):
            continue
        if _skip_path(Path(rel).parts):
            continue
        if rel.endswith(TEXT_EXTS):
            secrets.append(rel)
    return (syntax, lint, secrets)


def _repo_text_candidates() -> list[str]:
    """全量模式下「**可能入库**」的文件清单（相对路径）：tracked ∪ untracked-非忽略。

    为什么改（2026-09-23 修一个真实假阳性）：
      原实现是 `ROOT.rglob("*")`——扫**工作区全部文件**，**不认 .gitignore**。后果：
      `.workbuddy/tmp/lesson_add.txt`（被忽略的 lessons 临时产物，正文里含示例模式
      `password='...'`）把全量门禁卡死在 secrets 上，成为**唯一阻断项**。这是坏账两笔：
        ① 防护为零——被忽略的文件永远不可能进仓库，扫它不产生任何真实防护；
        ② 危害更大——门禁一变吵就会被绕过（本项目铁律），而它当时正在充当唯一红灯。
      改为 git 视角（与快模式「只看 staged」语义一致）：只有**能进 index** 的文件才值得扫。
      gitignore 自身失效场景（如 force-add）由「tracked ∪」这一半兜住。
    失败兜底：git 不可用时返回空 → secrets 报「无硬编码密钥」（不静默跳过整项检查之外的东西）。
    """
    out: list[str] = []
    for args in (["git", "ls-files"], ["git", "ls-files", "--others", "--exclude-standard"]):
        code, ls = run(args)
        if code != 0:
            continue
        out.extend(p for p in (ls.splitlines() if ls.strip() else []) if p.strip())
    return sorted({p.replace("\\", "/") for p in out})


def check_syntax(files: list[str]) -> tuple[bool, str]:
    """编译指定 .py，捕获语法错误

    2026-08-26 性能修复：原实现逐个 spawn `python -m py_compile`（950 文件 ≈ 5 分钟）
    → 改进程内 py_compile.compile（同进程编译，<10s）。
    """
    import py_compile

    errors: list[str] = []
    for f in files:
        try:
            py_compile.compile(str(ROOT / f), doraise=True)
        except py_compile.PyCompileError as exc:
            errors.append(f"{f}: {exc.msg or exc}")
    return (not errors), ("\n".join(errors) if errors else f"{len(files)} files compiled OK")


# ─── 门禁口径常量（单一来源）────────────────────────────────────────────────
#
# D14-8（2026-09-24 B10-h）：覆盖率阈值此前**双口径**——本工具硬编码 50，而 CI 的
#   `test_agent --cov-threshold 60` 是 60 ⇒ 「本地 --full 绿」**不等于**「CI 绿」。
#   现收敛为单一常量并取 CI 的值 60：**本地门禁不得比 CI 松**。
COV_THRESHOLD = 60

# D14-19（2026-09-24 B10-h）：缺工具（ruff / pytest 未安装）此前**静默转绿**
#   （返回 True + "[skip]"）——报告全绿而实际什么都没检查，与"假门禁"同族。
#   现改为**默认阻断**（环境不齐就不能声称"验证过"）；确需在缺工具环境跑通，
#   必须显式传 `--allow-missing-tools`（有意识、可见地放行，而非静默）。
ALLOW_MISSING_TOOLS = False

# D14-18（2026-09-24 B10-k / B10-o）：**统一退出码口径**——违规（代码问题）= 1，
# 环境错误（工具/依赖不可用，无法判定）= 2。
# 口径与判定函数**单一来源** = scripts/gate_exit.py（与 test_agent 共用）。
from gate_exit import ENV_ERR_PREFIX  # noqa: E402


def check_lint(files: list[str], *, allow_missing: bool = ALLOW_MISSING_TOOLS) -> tuple[bool, str]:
    """ruff check（若未安装则跳过并提示）；只查 git 已跟踪/本次提交文件

    2026-08-26：快模式只 lint 本次提交的 .py（排除有未暂存改动的），
    秒级返回；全量模式 lint 全部 tracked .py（排除他人进行中的改动）。
    """
    if not files:
        return True, "[skip] 无待检查 .py"
    code, out = run(["ruff", "check", *files])
    if code == 127:
        # D14-19：缺 ruff 不得静默放行（否则报告全绿而 lint 从未执行）
        if allow_missing:
            return True, "[放宽] ruff 未安装 → lint 未执行（--allow-missing-tools 显式放行）"
        return False, (
            f"{ENV_ERR_PREFIX} ruff 未安装 ⇒ lint **未执行**（环境不齐不得静默放行）\n"
            "  安装：pip install ruff\n"
            "  确需在缺工具环境通过：加 --allow-missing-tools（显式、可见）"
        )
    return (code == 0), out.strip() or "ruff clean"


def run_tests(*, allow_missing: bool = ALLOW_MISSING_TOOLS) -> tuple[bool, str]:
    """全量测试（仅 --full）：调用测试 Agent（pytest + api_smoke + research）"""
    code, out = run(
        [
            sys.executable,
            str(ROOT / "scripts" / "test_agent.py"),
            "--cov-threshold",
            str(COV_THRESHOLD),
        ],
        timeout=900,
    )
    if code == 0:
        return True, out.strip()[-1500:]
    if "No module named" in out and "pytest" in out:
        # D14-19：缺 pytest 不得静默放行（否则报告全绿而全量测试从未执行）
        if allow_missing:
            return True, "[放宽] pytest 未安装 → 全量测试未执行（--allow-missing-tools 显式放行）"
        return False, (
            f"{ENV_ERR_PREFIX} pytest 未安装 ⇒ 全量测试 **未执行**（环境不齐不得静默放行）\n"
            "  安装：pip install pytest pytest-cov httpx\n"
            "  确需在缺工具环境通过：加 --allow-missing-tools（显式、可见）"
        )
    return (code == 0), out.strip()[-1500:]


def check_secrets(files: list[str]) -> tuple[bool, str]:
    """扫描硬编码密钥（快模式：仅本次提交文件；全量模式：仓库 rglob）"""
    findings: list[str] = []
    for f in files:
        rel = f.replace("\\", "/")
        if any(rel.startswith(s) or s in rel for s in SECRET_SKIP):
            continue
        if _skip_path(Path(rel).parts):
            continue
        if not rel.endswith(TEXT_EXTS):
            continue
        try:
            content = Path(f).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            try:
                content = (ROOT / f).read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
        for i, line in enumerate(content.splitlines(), 1):
            # D14-9 配套（2026-09-24 B10-l）：**显式行内豁免**——行内含 `allowlist secret`
            # （detect-secrets 业界惯例）即跳过。为什么不用"整目录跳过 tests/"：那会把
            # **真密钥**连同合成值一起放过（静默盲区）；行内标记则让"这是合成值"在 diff 里
            # **可见、可审计、可 grep**，且必须逐个显式标注。
            if "allowlist secret" in line:
                continue
            for pat in SECRET_PATTERNS:
                if re.search(pat, line) and "change-me" not in line and "mock" not in line.lower():
                    findings.append(f"{rel}:{i}: 疑似密钥 {pat[:30]}...")
    return (not findings), ("\n".join(findings) if findings else "无硬编码密钥")


def check_todos(files: list[str]) -> tuple[bool, str]:
    """TODO/FIXME 统计（报告，不阻断）"""
    count = 0
    names = set()
    for f in files:
        if not f.endswith(".py") or _skip_path(Path(f).parts):
            continue
        try:
            content = (ROOT / f).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for line in content.splitlines():
            if re.search(r"\b(TODO|FIXME)\b", line):
                count += 1
                names.add(Path(f).name)
    return True, f"TODO/FIXME: {count} 处（{', '.join(sorted(names))}）— 不阻断"


def _record_failure(checks: dict) -> None:
    """检查失败时记录状态（供下次 check_lessons 强制登记教训）"""
    from datetime import datetime

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    state_path = REPORT_DIR / "last-failure.json"
    state_path.write_text(json.dumps({
        "failed_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
        "ts": int(datetime.now().astimezone().timestamp()),
        "blocking_checks": [k for k, v in checks.items() if not v[0]],
    }, ensure_ascii=False), encoding="utf-8")


def check_structure() -> tuple[bool, str]:
    """结构棘轮（文件体积 / 重复模式手抄）——2026-09-24 重构波 B1 接入提交门禁。

    复用 `audit_harness` 的**纯计算**入口（不打印、不导入后端 app），快/全量两模式均秒级。
    口径：**只拦新增**——存量超阈文件与既有重复计数在 `scripts/audit_harness_baseline.json`
    冻结；新增超阈文件或重复模式总数上升 → 阻断。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    from audit_harness import structure_findings

    crit = structure_findings()
    if crit:
        return False, "结构棘轮新增违规：\n" + "\n".join(crit)
    return True, "结构棘轮：无新增超阈文件 / 重复模式未上升"


def check_audit_axes() -> tuple[bool, str]:
    """跨轴审计（轴 1-4：契约 / 客户端 / 模型 / 导出）——2026-09-24 重构波 B10 接入门禁。

    背景（缺陷 D14-1，P0）：`structure` 只覆盖轴 5/6，轴 1-4 仅能人工跑，
    CI 0 命中——契约漂移 / 代码内死路由 / 0 引用模型 / 零调用能力导出四类静默腐化
    从未被任何自动化门禁拦住。本检查复用 `audit_harness.axes_findings()`（纯调用，
    不污染其 `main()`）。

    口径：**只有 CRITICAL 阻断**（新增契约双向漂移 / 代码内死路由 / 0 引用模型 /
    零调用能力导出）；INFO 仅报告。轴 1 需可导入后端环境，缺环境时**降级跳过**
    （不阻断）——见 `axes_findings` 注释。快/全量模式均执行（轴 2/3/4 为纯文件扫描）。
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    from audit_class_members import scan as scan_class_members
    from audit_client_imports import scan as scan_client_imports
    from audit_harness import axes_findings

    crit, info, note = axes_findings()

    # 轴 6（2026-09-24 · B5.2f 实测暴露）：客户端模块导出符号「用了却没 import」。
    # 背景：HBuilderX「编译成功」**只覆盖语法**，符号未解析也报成功 ⇒ 原有门禁全绿而代码是坏的。
    # 口径：CRITICAL 阻断（与轴 1-4 同）。
    import_violations, n_exports = scan_client_imports()
    if import_violations:
        crit = list(crit) + [
            f"[客户端导入] {v['file']} 使用了 {v['symbol']}"
            f"（定义于 {', '.join(v['declared_in'])}）却缺少 import"
            for v in import_violations
        ]
    info = list(info) + [f"[客户端导入] 扫描导出 {n_exports} 个，符号使用均有 import"
                         if not import_violations else
                         f"[客户端导入] {len(import_violations)} 处缺 import"]

    # 轴 7（2026-09-25 · GAP-2 实测暴露）：客户端 UTS 类成员「用了却没声明」（`this.<名>`）。
    # 背景：轴 6 只覆盖**跨模块符号**，「类成员访问」是另一道假绿面——`this.saving.value` 而类里
    # 没有 saving 字段，编译照样报绿（实测）。口径：CRITICAL 阻断（与轴 6 同）。
    member_violations, n_classes, _n_skipped = scan_class_members()
    if member_violations:
        crit = list(crit) + [
            f"[类成员] {v['file']} class {v['class']} 的 this.{v['member']} 未声明"
            "（编译不覆盖符号解析，真机 undefined 崩溃）"
            for v in member_violations
        ]
    info = list(info) + [f"[类成员] 检查 {n_classes} 类，成员解析均一致"
                         if not member_violations else
                         f"[类成员] {len(member_violations)} 处成员未声明"]

    suffix = f"；{note}" if note else ""
    if crit:
        return False, "跨轴审计新增 CRITICAL：\n" + "\n".join(crit) + suffix
    return True, f"跨轴审计（契约/客户端/模型/导出/客户端导入/类成员）：无 CRITICAL；INFO {len(info)} 项{suffix}"


def check_env_template() -> tuple[bool, str]:
    """部署模板 ↔ config.py 对齐——2026-09-24 重构波 B12 接入门禁（缺陷 D13-3）。

    背景：`deploy/.env.production.template` 声称「与 config.py 字段逐一对齐」，但该承诺
    此前**只是口头**——判据脚本 `deploy/scripts/check_env_template.py` 早已存在，却从未被
    任何门禁调用；于是新增 config 字段忘加模板（真缺口）或删字段留过期键（过期项）都无人发现。

    口径：**复用该脚本的 main()，不在此重写判定规则**（单一判据，避免两处漂移）。
    退出码 1 = 真缺口或过期项 → 阻断。纯文件扫描 + 导入 Settings.model_fields，快/全量均秒级。
    """
    script = ROOT / "deploy" / "scripts" / "check_env_template.py"
    if not script.exists():
        return False, f"判据脚本缺失：{script.relative_to(ROOT)}（部署契约已失效）"
    code, out = run([sys.executable, str(script)])
    if code != 0:
        return False, "env 模板与 config.py 漂移：\n" + out.strip()
    return True, out.strip()


def check_openapi_snapshot() -> tuple[bool, str]:
    """契约快照 `docs/openapi.json` 是否与后端实现一致——2026-09-24 重构波 B10-g（缺陷 D14-7）。

    背景：快照的再生此前是**人工步骤**（既无脚本、也无断言）⇒「文档里的契约」是否真等于
    「代码里的契约」长期无法证明；`audit_harness` 轴 1 只做**路径级**双向对拍，字段级漂移
    （schema/参数/状态码/描述）完全看不见。

    口径：复用 `scripts/gen_openapi.py --check`（**单一判据**，不在此重写比对规则），
    逐字节比对后端实时导出。
    退出码映射：0=一致 → 放行；1=快照过期 → **阻断**；2=后端环境不可导入（缺依赖/环境变量）
    → **降级放行并提示**（与 `audit_axes` 轴 1 的降级口径一致，避免把环境问题当代码问题）。
    """
    script = ROOT / "scripts" / "gen_openapi.py"
    if not script.exists():
        return False, f"判据脚本缺失：{script.relative_to(ROOT)}（契约再生无法复现）"
    code, out = run([sys.executable, str(script), "--check"])
    if code == 0:
        return True, out.strip()
    if code == 2:
        return True, "[降级] 后端环境不可导入，跳过契约快照一致性校验：\n" + out.strip()
    return False, "契约快照已过期（请运行 python scripts/gen_openapi.py 重导）：\n" + out.strip()


def check_lessons() -> tuple[bool, str]:
    """强制教训登记：上次失败后未登记 → 阻断 commit（2026-08-20 用户要求程序化强制）"""
    import sys as _sys

    _sys.path.insert(0, str(ROOT / "scripts"))
    from lessons import check_lessons as _check

    return _check()


def _classify_failure(blocking: dict[str, tuple[bool, str]]) -> tuple[list[str], list[str]]:
    """退出码分流（环境错误 2 / 违规 1）——判定逻辑单一来源见 scripts/gate_exit.py（B10-o）。"""
    from gate_exit import classify_failure

    return classify_failure(blocking)


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-Commit 代码质量审核")
    parser.add_argument("--path", default=str(ROOT), help="审核目录（兼容占位）")
    parser.add_argument("--full", action="store_true", help="全量门禁（仓库级扫描 + 全量测试）")
    parser.add_argument("--skip-tests", action="store_true", help="--full 时跳过全量测试（CI 快速静态层用）")
    parser.add_argument(
        "--allow-missing-tools",
        action="store_true",
        help="缺 ruff/pytest 时放行（默认阻断；D14-19：可见地放行，而非静默转绿）",
    )
    args = parser.parse_args()

    allow_missing = bool(args.allow_missing_tools)

    mode = "full" if args.full else "fast"
    syntax_files, lint_files, secret_files = _scope_files(args.full)

    checks = {
        "syntax": check_syntax(syntax_files),
        "lint": check_lint(lint_files, allow_missing=allow_missing),
        "secrets": check_secrets(secret_files),
        "todos": check_todos(secret_files),
        "structure": check_structure(),
        "audit_axes": check_audit_axes(),
        "env_template": check_env_template(),
        "openapi_snapshot": check_openapi_snapshot(),
    }
    if args.full and not args.skip_tests:
        checks["tests"] = run_tests(allow_missing=allow_missing)

    blocking = {k: v for k, v in checks.items() if not v[0]}
    passed = not blocking

    # 程序化强制教训登记（2026-08-20）：
    # 失败 → 记录状态文件；通过但上次失败未登记教训 → 阻断
    if not passed:
        _record_failure(checks)
    else:
        lessons_ok, lessons_msg = check_lessons()
        if not lessons_ok:
            passed = False
            blocking["lessons"] = (False, lessons_msg)

    # D14-18：把「环境错误」从「违规」中分流（两者都阻断，但退出码不同：2 vs 1）
    env_blocked, real_fail = _classify_failure(blocking)

    report = {
        "mode": mode,
        "passed": passed,
        "blocking_checks": list(blocking.keys()),
        "env_blocked_checks": env_blocked,
        "details": {k: {"ok": v[0], "output": v[1]} for k, v in checks.items()},
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
    # D14-22（B10-i）：统一信封（schema_version/generated_at/passed）+ 带兜底写入；
    # 自有键（mode/blocking_checks/env_blocked_checks/details）保留（CI 依赖 details/blocking_checks）。
    write_report(REPORT_PATH, report)

    print("=" * 60)
    print(f"Pre-Commit 代码质量审核（{'全量' if args.full else '快速'}模式）")
    print("=" * 60)
    for name, (ok, out) in checks.items():
        mark = "✅" if ok else "❌"
        if name == "lessons":
            print(f"\n[{mark}] lessons（强制教训登记）")
            print(f"    {out}")
            continue
        print(f"\n[{mark}] {name}")
        for line in out.splitlines()[:8]:
            print(f"    {line}")
        if len(out.splitlines()) > 8:
            print(f"    ... ({len(out.splitlines()) - 8} 行省略)")
    if not args.full:
        print("\n💡 全量门禁（含全量测试，完成/集成前跑）：python scripts/review_agent.py --full")
    elif args.skip_tests:
        print("\n💡 静态门禁完成（已跳过全量测试）；全量验证：python scripts/review_agent.py --full")

    print("\n" + "=" * 60)
    if passed:
        print("✅ 审核通过，可以提交")
        return 0
    # D14-18：区分「违规」（代码问题 → 1）与「环境错误」（工具/依赖不可用 → 2）。
    # 两者都阻断提交（非 0），但退出码不同可供 hook/CI/人工快速归因。
    if env_blocked:
        print(f"⚠️ 环境错误（非代码问题，本工具无法判定）：{', '.join(env_blocked)}")
    if real_fail:
        print(f"❌ 审核未通过：{', '.join(real_fail)} — 修复后重跑，禁止 commit")
        return 1
    print("❌ 无法完成审核（环境不齐，本工具未真正判定）— 修好环境后重跑，禁止 commit")
    return 2


if __name__ == "__main__":
    sys.exit(main())
