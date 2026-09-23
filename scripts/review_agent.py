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

退出码：0 = 通过可提交；1 = 存在阻断项（禁止 commit，先修复）。

报告输出：.cowork-temp/review-report.json（每次覆盖）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

# Windows 控制台 GBK 兼容（✅/❌ 为 Unicode）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# subprocess 输出按 UTF-8 解码（Windows 默认 GBK 会炸）
SUB_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / ".cowork-temp"
REPORT_PATH = REPORT_DIR / "review-report.json"

# 阻断规则：匹配到这些模式的代码不允许提交
SECRET_PATTERNS = [
    "sk-[A-Za-z0-9]{20,}",        # OpenAI/DeepSeek 风格 key
    "AKIA[0-9A-Z]{16}",           # AWS access key
    "-----BEGIN (RSA|EC|OPENSSH) PRIVATE KEY-----",
    "password\\s*=\\s*['\"][^'\"]+['\"]",
    "secret\\s*=\\s*['\"][^'\"]{8,}['\"]",
    # 2026-08-27（批次 H2 R7 · T9）：补齐常见密钥形态盲区
    "ghp_[A-Za-z0-9]{36}",                       # GitHub PAT
    "github_pat_[A-Za-z0-9_]{22,}",              # GitHub fine-grained PAT
    "xox[baprs]-[A-Za-z0-9-]{10,}",              # Slack token
    "AIza[0-9A-Za-z_-]{35}",                     # Google API key
    "sk_live_[0-9a-zA-Z]{20,}",                  # Stripe secret key
    "rk_live_[0-9a-zA-Z]{20,}",                  # Stripe restricted key
    "glpat-[A-Za-z0-9-]{20,}",                   # GitLab PAT
    # 通用配置键赋值（小写精确匹配，误报面已用仓库全量探测验证 = 0）
    "(client_secret|access_token|api[_-]?key|secret_key|private_key)\\s*=\\s*['\"][^'\"]{8,}['\"]",
]
SECRET_SKIP = {".env.example", ".git", "review_agent.py", "config.py"}
# 2026-08-27（T9）：补齐 client 工具链（已整目录排除）之外的文本类型盲区——
# .txt/.properties/.ps1/.sh/.mjs/.bat/.xml/.gitignore/.example/.mako 均可能携带密钥
TEXT_EXTS = (
    ".py", ".md", ".json", ".yaml", ".yml", ".toml", ".env", ".ini", ".sql",
    ".ts", ".uts", ".uvue",
    ".txt", ".properties", ".ps1", ".sh", ".mjs", ".bat", ".xml", ".gitignore",
    ".example", ".mako",
)


def run(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, cwd=cwd or ROOT,
            timeout=timeout, encoding="utf-8", errors="replace", env=SUB_ENV, check=False,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


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


def check_lint(files: list[str]) -> tuple[bool, str]:
    """ruff check（若未安装则跳过并提示）；只查 git 已跟踪/本次提交文件

    2026-08-26：快模式只 lint 本次提交的 .py（排除有未暂存改动的），
    秒级返回；全量模式 lint 全部 tracked .py（排除他人进行中的改动）。
    """
    if not files:
        return True, "[skip] 无待检查 .py"
    code, out = run(["ruff", "check", *files])
    if code == 127:
        return True, "[skip] ruff 未安装（pip install ruff 后启用）"
    return (code == 0), out.strip() or "ruff clean"


def run_tests() -> tuple[bool, str]:
    """全量测试（仅 --full）：调用测试 Agent（pytest + api_smoke + research）"""
    code, out = run(
        [sys.executable, str(ROOT / "scripts" / "test_agent.py"), "--cov-threshold", "50"],
        timeout=900,
    )
    if code == 0:
        return True, out.strip()[-1500:]
    if "No module named" in out and "pytest" in out:
        return True, "[skip] pytest 未安装（pip install pytest pytest-cov httpx）"
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
    from audit_harness import axes_findings

    crit, info, note = axes_findings()
    suffix = f"；{note}" if note else ""
    if crit:
        return False, "跨轴审计新增 CRITICAL：\n" + "\n".join(crit) + suffix
    return True, f"跨轴审计（契约/客户端/模型/导出）：无 CRITICAL；INFO {len(info)} 项{suffix}"


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


def main() -> int:
    parser = argparse.ArgumentParser(description="Pre-Commit 代码质量审核")
    parser.add_argument("--path", default=str(ROOT), help="审核目录（兼容占位）")
    parser.add_argument("--full", action="store_true", help="全量门禁（仓库级扫描 + 全量测试）")
    parser.add_argument("--skip-tests", action="store_true", help="--full 时跳过全量测试（CI 快速静态层用）")
    args = parser.parse_args()

    mode = "full" if args.full else "fast"
    syntax_files, lint_files, secret_files = _scope_files(args.full)

    checks = {
        "syntax": check_syntax(syntax_files),
        "lint": check_lint(lint_files),
        "secrets": check_secrets(secret_files),
        "todos": check_todos(secret_files),
        "structure": check_structure(),
        "audit_axes": check_audit_axes(),
        "env_template": check_env_template(),
        "openapi_snapshot": check_openapi_snapshot(),
    }
    if args.full and not args.skip_tests:
        checks["tests"] = run_tests()

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

    report = {
        "mode": mode,
        "passed": passed,
        "blocking_checks": list(blocking.keys()),
        "details": {k: {"ok": v[0], "output": v[1]} for k, v in checks.items()},
        "generated_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

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
    print(f"❌ 审核未通过：{', '.join(blocking)} — 修复后重跑，禁止 commit")
    return 1


if __name__ == "__main__":
    sys.exit(main())
