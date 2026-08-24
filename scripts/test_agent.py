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


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> tuple[int, str]:
    sub_env = {**SUB_ENV, **(env or {})}
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, cwd=cwd or ROOT,
            timeout=600, encoding="utf-8", errors="replace", env=sub_env, check=False,
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


def _free_memory_gb() -> float:
    """可用物理内存 GB（Windows GlobalMemoryStatusEx）；探测失败返回 -1"""
    try:
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
            return stat.ullAvailPhys / (1024**3)
    except Exception:  # noqa: BLE001 —— 探测失败不阻断（无可用内存信息）
        return -1.0
    return -1.0


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
    """API 真实用例冒烟（2026-08-24 用户拍板：替代原最小冒烟）

    真实用户旅程：认证安全 / 文字全链路（创建→管线→分类→搜索命中）/
    照片 multipart 上传链路 / 时间轴结构 / 去重 409。详见 scripts/api_smoke_cases.py。
    2026-08-25 内存优化：smoke 进程跳过 reranker 加载（RERANKER_MODEL=__disabled__，
    命中存在性检查即降级原序）——rerank 覆盖在 pytest -m rag；smoke 峰值降 ~0.5-1GB。
    """
    code, out = run(
        [sys.executable, str(ROOT / "scripts" / "api_smoke_cases.py")],
        env={"RERANKER_MODEL": "__disabled__"},
    )
    return (code == 0), out.strip()[-1500:]


def run_research_validation() -> tuple[bool, str]:
    """事件聚合原型验证（POC-05）"""
    cmd = (
        "import sys; "
        "sys.path.insert(0, r'" + str(ROOT / "backend") + "'); "
        "from app.services.event_aggregation.run_validation import main; main()"
    )
    code, out = run([sys.executable, "-c", cmd])
    return (code == 0), out.strip()[-1500:]


def main() -> int:
    parser = argparse.ArgumentParser(description="代码测试 Agent")
    parser.add_argument("--cov-threshold", type=int, default=60, help="覆盖率阈值（默认 60）")
    parser.add_argument("--only", choices=["api", "research"], help="只跑部分")
    args = parser.parse_args()

    free = _free_memory_gb()
    if free >= 0:
        print(f"可用物理内存: {free:.1f}GB")
        if args.only is None and free < 4.0:
            print(
                "⚠ 可用内存 <4GB：重模型阶段（api_smoke 峰值 ~2.5GB）有 OOM 风险——"
                "建议先关闭 HBuilderX 编译残留/TRAE/WorkBuddy 等大进程"
            )

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
