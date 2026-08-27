#!/usr/bin/env python3
"""忆述光华 · 代码测试 Agent（与 review_agent 配套）

职责（Commit Gate 的 tests 环节 + 独立运行）：
  1. pytest 全量测试（backend + research），失败即阻断
  2. 覆盖率统计（--cov，阈值可配；2026-08-27 起 rag 分组在环境就绪时计入覆盖率，
     产出 .cowork-temp/coverage.xml；环境缺失时显式警告而非静默漏跑）
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


def _port_open(port: int, host: str = "127.0.0.1") -> bool:
    """环境自检：端口是否可达（docker 容器 yishu-redis:6379 / yishu-qdrant:6333）"""
    import socket

    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


COV_XML = ".cowork-temp/coverage.xml"


def _rag_env_ready() -> tuple[bool, str]:
    """RAG 用例运行环境探测（2026-08-27 批次 H2：rag 纳入覆盖率前先确认可跑）

    探测两点：Qdrant 6333 端口可达 + BGE-M3 已入 HF 缓存。
    rag 用例重（19 个，加载 BGE-M3 ~1.2GB / 现场下载 2.2GB），环境缺失时
    显式警告跳过（不现场下载、不挂起），保持本地门禁秒级可预测。
    """
    import os

    reasons: list[str] = []
    if not _port_open(6333):
        reasons.append("Qdrant 6333 端口不可达（需 docker 起 yishu-qdrant）")
    hub = os.environ.get("HF_HUB_CACHE")
    if not hub:
        hf_home = os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
        hub = str(Path(hf_home) / "hub")
    if not (Path(hub) / "models--BAAI--bge-m3").is_dir():
        reasons.append(f"BGE-M3 未入 HF 缓存（先跑 python scripts/warm_hf_models.py；缓存目录 {hub}）")
    if reasons:
        return False, "；".join(reasons)
    return True, ""


def _emit_warnings(warnings: list[str]) -> None:
    """deselect 显式警告（2026-08-27 批次 H2：防静默漏跑）——stdout + workflow annotation

    `::warning::` 在本地仅是普通行；在 CI 会被 GitHub 识别为 warning annotation
    （匿名 API 可读），避免测试被静默 deselect 却不留痕迹。
    """
    for w in warnings:
        esc = w.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::warning::{esc}")
        print(f"[deselect 警告] {w}")


def run_pytest(cov_threshold: int) -> tuple[bool, str]:
    """pytest 全量 + 覆盖率（2026-08-25：测试环境封闭——覆盖 MOCK/STORAGE_BACKEND

    .env 可能配真实服务（MOCK_EXTERNAL_AI=false / STORAGE_BACKEND=fs），
    测试套件按 mock+fake 断言；不覆盖会因 .env 状态导致 test_amap/test_asr
    等 mock 断言失败（review_agent 全量门禁暴露）。
    2026-08-26：环境自检——docker 容器（yishu-redis/yishu-qdrant）未启动时
    deselect 已知依赖项，避免环境缺失卡住 commit（CI 仍全量验证）。
    2026-08-27（批次 H2 R7）：
      - T3 覆盖率统一 60：主套件 + rag 分组（环境就绪时 --cov-append）合并判定阈值；
      - T6 deselect 显式警告：环境缺失 deselect / rag 未计入覆盖率时输出显式警告；
      - T8 coverage.xml 产物：写 .cowork-temp/coverage.xml（CI 上传 artifact）。
    """
    import os

    env = {
        **dict(os.environ),
        "MOCK_EXTERNAL_AI": "true",
        "STORAGE_BACKEND": "fake",
    }
    # 注意：主套件不全局设 QDRANT_COLLECTION——conftest 的 vector_collection fixture
    # 已按需把默认 collection 指到 test_* 隔离库（TD-P1C）；rag 子进程单独设
    # test_yishu_contents 与 CI `pytest -m rag` 步骤的隔离形态保持一致。
    warnings: list[str] = []
    outputs: list[str] = []
    failed = False

    main_cmd = [
        sys.executable, "-m", "pytest", "backend/tests",
        "-q", "--tb=short",
        "--cov=backend/app",
        "--cov-report=term-missing",
    ]
    if not _port_open(6379):
        main_cmd += ["--deselect", "tests/test_queue.py::test_redis_connection"]
        main_cmd += ["--deselect", "tests/test_queue.py::test_enqueue_and_worker_consume"]
        main_cmd += ["--deselect", "tests/test_queue.py::test_queue_failure_goes_to_dead"]
        warnings.append("Redis 6379 不可达 → deselect 3 个 test_queue 依赖用例（CI 仍全量验证）")
    if not _port_open(6333):
        main_cmd += ["--deselect", "tests/test_pipeline.py::TestPhotoPipeline::test_photo_caption_and_done"]
        main_cmd += ["--deselect", "tests/test_pipeline.py::TestPhotoPipeline::test_photo_writes_image_vec"]
        warnings.append("Qdrant 6333 不可达 → deselect 2 个 test_pipeline 向量用例（CI 仍全量验证）")

    rag_ready, rag_reason = _rag_env_ready()
    if rag_ready:
        # 主套件先跑（不 gate 阈值），rag 追加覆盖后统一判定（合并覆盖 ≥ 主套件覆盖）
        code, main_out = run(main_cmd, env=env)
        outputs.append("[pytest·主套件]\n" + main_out.strip())
        if code != 0:
            failed = True
        rag_cmd = [
            sys.executable, "-m", "pytest", "backend/tests", "-m", "rag",
            "-q", "--tb=short",
            "--cov=backend/app",
            "--cov-append",
            "--cov-report=term-missing",
            f"--cov-report=xml:{COV_XML}",
            f"--cov-fail-under={cov_threshold}",
        ]
        code2, rag_out = run(rag_cmd, env={**env, "QDRANT_COLLECTION": "test_yishu_contents"})
        outputs.append("[pytest·rag 分组（计入覆盖率）]\n" + rag_out.strip())
        if code2 != 0:
            failed = True
    else:
        warnings.append(f"RAG 用例未纳入覆盖率统计（{rag_reason}）；覆盖率阈值仅按主套件判定")
        main_cmd += [f"--cov-fail-under={cov_threshold}", f"--cov-report=xml:{COV_XML}"]
        code, out = run(main_cmd, env=env)
        outputs.append("[pytest·主套件]\n" + out.strip())
        if code != 0:
            failed = True

    if any("No module named pytest" in o or "No module named pytest-cov" in o for o in outputs):
        last = outputs[-1].strip().splitlines()[-1] if outputs[-1].strip() else "pytest"
        return True, f"[skip] 缺依赖：{last}"

    _emit_warnings(warnings)
    combined = "\n\n".join(outputs)
    return (not failed), combined.strip()[-3500:]


def run_api_smoke() -> tuple[bool, str]:
    """API 真实用例冒烟（2026-08-24 用户拍板：替代原最小冒烟）

    真实用户旅程：认证安全 / 文字全链路（创建→管线→分类→搜索命中）/
    照片 multipart 上传链路 / 时间轴结构 / 去重 409。详见 scripts/api_smoke_cases.py。
    2026-08-25 内存优化：smoke 进程跳过 reranker 加载（RERANKER_MODEL=__disabled__，
    命中存在性检查即降级原序）——rerank 覆盖在 pytest -m rag；smoke 峰值降 ~0.5-1GB。
    TD-P1C（2026-08-26）：smoke 写 test_ 前缀测试 collection（QDRANT_COLLECTION），
    不再写生产 yishu_contents；与 pytest 隔离 → 执行顺序不再影响门禁结果。
    """
    code, out = run(
        [sys.executable, str(ROOT / "scripts" / "api_smoke_cases.py")],
        env={
            "RERANKER_MODEL": "__disabled__",
            "QDRANT_COLLECTION": "test_yishu_contents",
        },
    )
    if code != 0:
        # 存量 flaky（2026-08-24 起：Qdrant 累积数据后新内容可能被挤出 top-k）——
        # TD-P1C 后 smoke 已隔离于独立 collection，重试作为兜底保留。
        print("[retry] api_smoke 首次失败，自动重试一次")
        code, out = run(
            [sys.executable, str(ROOT / "scripts" / "api_smoke_cases.py")],
            env={
                "RERANKER_MODEL": "__disabled__",
                "QDRANT_COLLECTION": "test_yishu_contents",
            },
        )
    return (code == 0), out.strip()[-1500:]


def run_cleanup_test_collections() -> tuple[bool, str]:
    """每跑结束清理 test_ 前缀 Qdrant collection（尽力而为，失败不阻断门禁）

    TD-P1C（2026-08-26）：测试环境累计数据清理，防 test_ 库无界增长
    挤占/污染后续跑（api_smoke 起始也会清理，这里作为每跑收尾兜底）。
    """
    cmd = (
        "import sys; "
        "sys.path.insert(0, r'" + str(ROOT / "backend") + "'); "
        "from app.services.vector_store import cleanup_test_collections; "
        "print('removed:', cleanup_test_collections())"
    )
    code, out = run([sys.executable, "-c", cmd])
    return (code == 0), out.strip()[-500:]


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

    # 执行顺序（TD-P1C 2026-08-26）：api_smoke 与 pytest 均已隔离——
    # api_smoke 写 test_ 前缀测试 collection（不再写生产 yishu_contents），
    # 不再需要"api_smoke 先于 pytest"顺序 hack；pytest 常规先行，
    # 每跑结束统一清理 test_ collection（尽力而为）。
    sections = {
        "pytest": run_pytest(args.cov_threshold) if args.only is None or args.only == "api" else (True, "[skip]"),
        "api_smoke": run_api_smoke() if args.only is None or args.only == "api" else (True, "[skip]"),
        "research": run_research_validation() if args.only is None or args.only == "research" else (True, "[skip]"),
    }
    blocking = {k: v for k, v in sections.items() if not v[0]}
    passed = not blocking

    # 每跑结束清理测试 collection（尽力而为，失败不阻断门禁）
    cleanup_ok, cleanup_out = run_cleanup_test_collections()

    report = {
        "passed": passed,
        "blocking_sections": list(blocking.keys()),
        "details": {k: {"ok": v[0], "output": v[1]} for k, v in sections.items()},
        "cleanup_test_collections": {"ok": cleanup_ok, "output": cleanup_out},
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
    print(f"\n[{'✅' if cleanup_ok else '⚠'}] cleanup_test_collections")
    for line in cleanup_out.splitlines()[:3]:
        print(f"    {line}")
    print("\n" + "=" * 60)
    if passed:
        print("✅ 测试全部通过")
        return 0
    print(f"❌ 测试未通过：{', '.join(blocking)} — 修复后重跑")
    return 1


if __name__ == "__main__":
    sys.exit(main())
