#!/usr/bin/env python3
"""忆述光华 · Pre-Commit 代码质量审核 Agent（Sprint 1 新增流程）

用法：
  python scripts/review_agent.py          # 快速门禁（默认，秒级）：只查本次提交涉及的文件
  python scripts/review_agent.py --full   # 全量门禁：仓库级语法/lint/密钥扫描 + 全量测试（集成/CI 前跑）
  python scripts/review_agent.py --path <dir>   # （兼容占位，忽略）

职责（Commit Gate，写入 AGENTS.md）：
  快模式（pre-commit 默认，2026-08-26 拆分——原每次 commit 全量跑 5 分钟）：
    1. Python 语法编译检查（本次提交新增/修改的 .py）
    2. Lint（ruff，仅本次提交的 .py；有未暂存改动的文件跳过，语义同旧实现）
    3. 密钥与敏感信息扫描（仅本次提交文件——gitignore 的 .env 等永不入 index，天然豁免）
    4. TODO/FIXME 计数报告（不阻断）
    5. lessons 强制登记检查（上次失败未登记 → 阻断）
  全量模式（--full，完成验收/集成/CI 用，即旧行为）：
    1-4 同快模式但扫描整个仓库（排除 client/ 工具链）
    5. 全量测试（pytest + api_smoke + research，经 scripts/test_agent.py）
    6. lessons 强制登记检查

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
]
SECRET_SKIP = {".env.example", ".git", "review_agent.py", "config.py"}
TEXT_EXTS = (".py", ".md", ".json", ".yaml", ".yml", ".toml", ".env", ".ini", ".sql", ".ts", ".uts", ".uvue")


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
    """harness 工具链扫描排除（B2 决策：client/ 为 uni-app x，非 Python 工具链）"""
    return ".git" in parts or ".cowork-temp" in parts or "client" in parts


def _git_files(cached: bool) -> list[str]:
    """git diff 文件名（相对路径，正斜杠）；cached=True 查暂存区（本次提交），否则查工作区改动"""
    args = ["git", "diff", "--name-only", "--diff-filter=ACM"]
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
    """有未暂存改动的文件（工作区 != 暂存区）——跳过，避免 lint 到进行中的半成品"""
    return {p.replace("\\", "/") for p in _git_files(cached=False)}


def _scope_files(full: bool) -> tuple[list[str], list[str], list[str]]:
    """按模式计算三组扫描文件：(syntax, lint, secrets/todos)

    快模式：仅本次提交文件；全量模式：整个仓库（旧行为）。
    """
    if not full:
        staged = _staged_files()
        dirty = _dirty_files()
        # 已暂存但又有未暂存改动的文件跳过 lint（ruff 读工作区，可能 lint 到半成品）
        lint = [f for f in staged if f.endswith(".py") and f not in dirty]
        return ([f for f in staged if f.endswith(".py")], lint, staged)

    syntax: list[str] = []
    for p in ROOT.rglob("*.py"):
        if not _skip_path(p.parts):
            syntax.append(str(p.relative_to(ROOT)))
    code, ls = run(["git", "ls-files", "--", "*.py"])
    tracked = [p for p in (ls.splitlines() if code == 0 and ls.strip() else []) if p.strip()]
    dirty = _dirty_files()
    lint = [p for p in tracked if p not in dirty and p.replace("/", "\\") not in dirty]
    secrets: list[str] = []
    for f in ROOT.rglob("*"):
        if not f.is_file():
            continue
        rel = f.relative_to(ROOT).as_posix()
        if any(rel.startswith(s) or s in rel for s in SECRET_SKIP):
            continue
        if _skip_path(f.parts):
            continue
        if rel.endswith(TEXT_EXTS):
            secrets.append(str(f))
    return (syntax, lint, secrets)


def check_syntax(files: list[str]) -> tuple[bool, str]:
    """编译指定 .py，捕获语法错误"""
    errors: list[str] = []
    for f in files:
        code, out = run([sys.executable, "-m", "py_compile", str(ROOT / f)])
        if code != 0:
            errors.append(f"{f}: {out.strip()[:300]}")
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
    args = parser.parse_args()

    mode = "full" if args.full else "fast"
    syntax_files, lint_files, secret_files = _scope_files(args.full)

    checks = {
        "syntax": check_syntax(syntax_files),
        "lint": check_lint(lint_files),
        "secrets": check_secrets(secret_files),
        "todos": check_todos(secret_files),
    }
    if args.full:
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

    print("\n" + "=" * 60)
    if passed:
        print("✅ 审核通过，可以提交")
        return 0
    print(f"❌ 审核未通过：{', '.join(blocking)} — 修复后重跑，禁止 commit")
    return 1


if __name__ == "__main__":
    sys.exit(main())
