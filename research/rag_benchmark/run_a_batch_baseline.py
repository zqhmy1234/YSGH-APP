"""A 批检索基线（可复跑的 JSON 产物）

背景（2026-11-17）：A 批首个基线（2026-11-08，GPU）只写在本手册 v9 的文字里，
没有独立 JSON；命令、环境指纹、逐条命中与失败 query id 都无法 diff。
本脚本把 run_eval 的 `--truth-a --truth-corpus --details` 包一层，输出：

  research/rag_benchmark/a_batch_baseline_<YYYYMMDD>.json

包含：
  - meta：命令、时间、git 分支/commit/是否 dirty、环境指纹、数据文件 sha256；
  - truth：run_eval 的完整真值报告（含 details / failed_query_ids）。

用法（在 <仓库> 根目录，用 backend 同一个 Python）：
    python -m research.rag_benchmark.run_a_batch_baseline
    python -m research.rag_benchmark.run_a_batch_baseline --out research/rag_benchmark/a_batch_baseline.json
    python -m research.rag_benchmark.run_a_batch_baseline --no-gpu   # 强制 CPU

注意：A 批只有 10 条查询、5 条有 expected、4 条负样本；**这不是回归基线**，
只用于「现在能算出一个可复核的数」与后续 diff。门禁 FAIL 是预期结果。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[2]
BENCH = REPO / "research" / "rag_benchmark"
A_REMAPPED = REPO / "research" / "truth-data" / "a" / "a_remapped.json"
CORPUS_FILES = [
    BENCH / "truth_corpus" / "corpus_b_truth.json",
    BENCH / "truth_corpus" / "corpus_c_truth.json",
    BENCH / "truth_corpus" / "corpus_manifest.json",
]


def _rel(path: Path) -> str:
    """仓库内路径用相对路径；仓库外保留原样（文档不写死盘符的约定）。"""
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _git(*args: str) -> str:
    try:
        git_bin = shutil.which("git") or "git"
        out = subprocess.run(  # noqa: S603 —— 固定 git 子命令，无用户输入拼接
            [git_bin, *args], cwd=REPO, capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=False,
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001 —— 非 git 环境不阻断评测
        return ""


def _gpu_available() -> bool:
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--out", default="",
        help="输出路径（默认 research/rag_benchmark/a_batch_baseline_<YYYYMMDD>.json）",
    )
    parser.add_argument("--no-gpu", action="store_true", help="强制 EMBED_DEVICE=cpu")
    args = parser.parse_args()

    out = Path(args.out) if args.out else (
        BENCH / f"a_batch_baseline_{datetime.now(tz=UTC).astimezone().strftime('%Y%m%d')}.json"
    )
    out = out if out.is_absolute() else (REPO / out)

    env = dict(os.environ)
    if args.no_gpu:
        env["EMBED_DEVICE"] = "cpu"
    else:
        env.setdefault("EMBED_DEVICE", "cuda" if _gpu_available() else "cpu")

    with tempfile.TemporaryDirectory(prefix="a_batch_") as td:
        tmp = Path(td) / "run_eval_report.json"
        cmd = [
            sys.executable, "-m", "research.rag_benchmark.run_eval",
            "--truth-a", "--truth-corpus", "--details", "--out", str(tmp),
        ]
        print("[a-baseline] 运行:", " ".join(cmd))
        print(f"[a-baseline] EMBED_DEVICE={env.get('EMBED_DEVICE')}")
        proc = subprocess.run(  # noqa: S603 —— cmd 固定，路径由 sys.executable 提供
            cmd, cwd=REPO, env=env, capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=False,
        )
        if proc.stdout:
            print(proc.stdout, end="")
        if proc.stderr:
            print(proc.stderr, file=sys.stderr, end="")
        if not tmp.is_file():
            print("[a-baseline] run_eval 未产出报告，退出", file=sys.stderr)
            return 2
        report = json.loads(tmp.read_text(encoding="utf-8"))

    payload = {
        "meta": {
            "name": "A 批检索基线（首个可复跑产物）",
            "generated_at": datetime.now(tz=UTC).astimezone().isoformat(),
            "command": (
                "python -m research.rag_benchmark.run_a_batch_baseline "
                f"--out {_rel(out)}"
            ),
            "eval_command": (
                "python -m research.rag_benchmark.run_eval "
                "--truth-a --truth-corpus --details --out <tmp>"
            ),
            "git": {
                "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
                "commit": _git("rev-parse", "HEAD"),
                "dirty": bool(_git("status", "--porcelain")),
            },
            "env": {
                "EMBED_DEVICE": env.get("EMBED_DEVICE"),
                "python": sys.version.split()[0],
            },
            "data": {
                "a_remapped": {
                    "path": str(A_REMAPPED.relative_to(REPO)),
                    "sha256": _sha256(A_REMAPPED),
                },
                "corpus": {
                    str(p.relative_to(REPO)): _sha256(p)
                    for p in CORPUS_FILES
                },
            },
            "note": (
                "A 批只有 10 条查询 / 5 条有 expected / 4 条负样本；"
                "不是回归基线，门禁 FAIL 是预期。"
            ),
        },
        "overall_pass": report.get("overall_pass"),
        "truth": report.get("truth"),
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[a-baseline] 已输出: {out}")
    gates = (payload.get("truth") or {}).get("gates") or {}
    print(
        f"[a-baseline] hit_rate@3={gates.get('hit_rate@3')} "
        f"negative_error_rate={gates.get('negative_error_rate')} "
        f"failed_query_ids={len((payload.get('truth') or {}).get('failed_query_ids') or [])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
