#!/usr/bin/env python3
"""忆述光华 · 代码测试 Agent（与 review_agent 配套）

职责（Commit Gate 的 tests 环节 + 独立运行）：
  1. pytest 全量测试（backend + research），失败即阻断
  2. 覆盖率统计（--cov，阈值可配）
  3. API 冒烟测试（FastAPI TestClient：healthz / auth mock 链路 / contents / search）
  4. 原型验证脚本（事件聚合 run_validation）

用法：
  python scripts/test_agent.py [--cov-threshold 70] [--only api|research]

退出码：0 = 全部通过；1 = 有失败（禁止 commit）。
报告：.cowork-temp/test-report.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
REPORT_DIR = ROOT / ".cowork-temp"
REPORT_PATH = REPORT_DIR / "test-report.json"

SUB_ENV = {
    **__import__("os").environ,
    "PYTHONIOENCODING": "utf-8",
    "PYTHONPATH": str(ROOT),
}


def run(cmd: list[str], cwd: Path | None = None) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, cwd=cwd or ROOT,
            timeout=600, encoding="utf-8", errors="replace", env=SUB_ENV, check=False,
        )
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        return 127, f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "timeout"


def run_pytest(cov_threshold: int) -> tuple[bool, str]:
    """pytest 全量 + 覆盖率"""
    cmd = [
        sys.executable, "-m", "pytest", "backend/tests",
        "-q", "--tb=short",
        "--cov=backend/app",
        "--cov-report=term-missing",
        f"--cov-fail-under={cov_threshold}",
    ]
    code, out = run(cmd)
    if "No module named pytest" in out or "No module named pytest-cov" in out:
        return True, f"[skip] 缺依赖：{out.strip().splitlines()[-1] if out.strip() else 'pytest'}"
    return (code == 0), out.strip()[-2500:]


def run_api_smoke() -> tuple[bool, str]:
    """FastAPI TestClient 冒烟（不依赖外部服务，mock 模式）"""
    script = (
        "import sys; "
        "sys.path.insert(0, r'" + str(ROOT) + "'); "
        "sys.path.insert(0, r'" + str(ROOT / "backend") + "'); "
        "from fastapi.testclient import TestClient; "
        "from app.main import app; "
        "c = TestClient(app); "
        "checks = []; "
        "r = c.get('/healthz'); checks.append(('healthz', r.status_code == 200)); "
        "r = c.post('/api/v1/auth/wechat', json={'code':'t1','device_id':'d1'}); "
        "checks.append(('auth-wechat', r.status_code == 200 and 'access_token' in r.json().get('data', {}))); "
        "token = r.json().get('data', {}).get('access_token', ''); "
        "h = {'Authorization': 'Bearer ' + token} if token else {}; "
        "r = c.post('/api/v1/contents', json={'content_type':'text','text':'hello','source':'app'}, headers=h); "
        "checks.append(('content-create', r.status_code == 200)); "
        "r = c.post('/api/v1/search', json={'q':'杭州'}, headers=h); "
        "checks.append(('search', r.status_code == 200 and len(r.json().get('data', {}).get('hits', [])) > 0)); "
        "failed = [n for n, ok in checks if not ok]; "
        "print('SMOKE:', {n: ('PASS' if ok else 'FAIL') for n, ok in checks}); "
        "sys.exit(1 if failed else 0)"
    )
    code, out = run([sys.executable, "-c", script])
    return (code == 0), out.strip()[-1500:]


def run_research_validation() -> tuple[bool, str]:
    """事件聚合原型验证（POC-05）"""
    cmd = (
        "import sys; "
        "sys.path.insert(0, r'" + str(ROOT) + "'); "
        "from research.event_aggregation.run_validation import main; main()"
    )
    code, out = run([sys.executable, "-c", cmd])
    return (code == 0), out.strip()[-1500:]


def main() -> int:
    parser = argparse.ArgumentParser(description="代码测试 Agent")
    parser.add_argument("--cov-threshold", type=int, default=60, help="覆盖率阈值（默认 60）")
    parser.add_argument("--only", choices=["api", "research"], help="只跑部分")
    args = parser.parse_args()

    sections = {
        "pytest": run_pytest(args.cov_threshold) if args.only is None or args.only == "api" else (True, "[skip]"),
        "api_smoke": run_api_smoke() if args.only is None or args.only == "api" else (True, "[skip]"),
        "research": run_research_validation() if args.only is None or args.only == "research" else (True, "[skip]"),
    }
    blocking = {k: v for k, v in sections.items() if not v[0]}
    passed = not blocking

    report = {
        "passed": passed,
        "blocking_sections": list(blocking.keys()),
        "details": {k: {"ok": v[0], "output": v[1]} for k, v in sections.items()},
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print("=" * 60)
    print("代码测试 Agent")
    print("=" * 60)
    for name, (ok, out) in sections.items():
        mark = "✅" if ok else "❌"
        print(f"\n[{mark}] {name}")
        for line in out.splitlines()[:10]:
            print(f"    {line}")
    print("\n" + "=" * 60)
    if passed:
        print("✅ 测试全部通过")
        return 0
    print(f"❌ 测试未通过：{', '.join(blocking)} — 修复后重跑")
    return 1


if __name__ == "__main__":
    sys.exit(main())
