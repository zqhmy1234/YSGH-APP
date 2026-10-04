#!/usr/bin/env python
"""预置本地模型资产（BGE-M3 等）到 backend/models/ —— 构建/部署/CI 前置步骤。

## 为什么需要这个脚本（2026-10-04）

`embedding.py` 优先读`backend/models/bge-m3/`（本地目录），缺失才回退 HF repo id。
但本机/新机器上模型常常缺失，而检索链路会在模型不可用时**静默降级**
（`degraded=true`、`hits=[]`）—— 表现为「搜索搜不到」，极易被误判成检索逻辑坏了。
实测 2026-10-04：`backend/models/` 与 HF 缓存全空 ⇒ 4 个检索测试集体失败，根因同源。

## 为什么走 ModelScope 而不是 huggingface.co

本机实测（2026-10-04）：
  - `huggingface.co`  → SSL `UNEXPECTED_EOF_WHILE_READING`（不可达）
  - `hf-mirror.com`   → 同上（**此前记录的"可用"已失效**）
  - `modelscope.cn`   → ✅ 可用

故以 ModelScope 为源。若你的网络能直连 HF，可加 `--source hf` 走 huggingface_hub。

## 用法

```bash
# 预置检索主塔（默认，约 2.2GB）
python scripts/prep_models.py

# 全部登记在册的模型
python scripts/prep_models.py --all

# 已完整则跳过（幂等，可重复跑）
python scripts/prep_models.py

# 指定源
python scripts/prep_models.py --source hf
```

下载中断会留下 `*.incomplete`，本脚本会检测并重试；网络不稳时自动多轮重试。
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"

# name -> (repo_id, 需要的权重文件后缀, 用途)
CATALOG: dict[str, tuple[str, tuple[str, ...], str]] = {
    "bge-m3": (
        "BAAI/bge-m3",
        (".bin", ".safetensors"),
        "RAG 检索主塔（dense+sparse）——缺失则检索整体降级",
    ),
}


def _has_weights(target: Path) -> bool:
    if not (target / "config.json").is_file():
        return False
    for suf in (".bin", ".safetensors"):
        for f in target.glob("*" + suf):
            if f.is_file() and f.stat().st_size > 50 * 1024 * 1024:  # >50MB 视为真权重
                return True
    return False


def _incomplete_files(target: Path) -> list[Path]:
    return [f for f in target.glob("*.incomplete")] if target.is_dir() else []


def _dir_mb(target: Path) -> float:
    return sum(f.stat().st_size for f in target.rglob("*") if f.is_file()) / 1048576


def fetch(name: str, repo: str, source: str, retries: int) -> bool:
    target = MODELS_DIR / name
    if _has_weights(target):
        print(f"  [skip] {name} 已完整（{_dir_mb(target):.1f} MB）")
        return True

    for attempt in range(1, retries + 1):
        for inc in _incomplete_files(target):
            print(f"  [clean] 清理中断残留 {inc.name}")
            inc.unlink(missing_ok=True)
        print(f"  [dl] {name} ← {repo}（第 {attempt}/{retries} 次，源={source}）", flush=True)
        t0 = time.monotonic()
        try:
            if source == "ms":
                from modelscope import snapshot_download

                snapshot_download(repo, local_dir=str(target))
            else:
                from huggingface_hub import snapshot_download

                snapshot_download(repo_id=repo, local_dir=str(target))
        except Exception as exc:  # noqa: BLE001 —— 网络失败要重试
            print(f"      失败 {type(exc).__name__}: {str(exc)[:90]}")
            if attempt < retries:
                time.sleep(3)
            continue
        if _has_weights(target):
            print(f"      完成 {_dir_mb(target):.1f} MB，用时 {time.monotonic() - t0:.0f}s")
            return True
        print("      下载结束但权重仍缺失（可能被allow_patterns 过滤）")
        return False
    return False


def main() -> int:
    ap = argparse.ArgumentParser(description="预置本地模型资产到 backend/models/")
    ap.add_argument("--all", action="store_true", help="预置全部登记模型")
    ap.add_argument("--source", choices=["ms", "hf"], default="ms",
                    help="下载源：ms=ModelScope（默认，本机唯一可用）/ hf=huggingface_hub")
    ap.add_argument("--retries", type=int, default=4, help="失败重试次数")
    ap.add_argument("names", nargs="*", help="指定模型名（默认 bge-m3）")
    args = ap.parse_args()

    targets = list(CATALOG) if args.all else (args.names or ["bge-m3"])
    MODELS_DIR.mkdir(parents=True, exist_ok=True)

    print("模型目录:", MODELS_DIR)
    print("下载源  :", "ModelScope" if args.source == "ms" else "HuggingFace")
    print()
    ok = True
    for name in targets:
        if name not in CATALOG:
            print(f"[skip] {name} 不在登记清单（CATALOG），请先登记到 models/README.md")
            continue
        repo, _, why = CATALOG[name]
        print(f"[model] {name} —— {why}")
        if not fetch(name, repo, args.source, args.retries):
            print(f"  [FAIL] {name} 未就绪")
            ok = False
    print()
    if ok:
        print("全部就绪。注意：模型目录已 gitignore，入库不会带上模型本体。")
        return 0
    print("部分模型未就绪。检索链路会**静默降级**（degraded=true、hits=[]），"
          "表现为「搜不到」而非报错——排查检索问题前先确认模型在位。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
