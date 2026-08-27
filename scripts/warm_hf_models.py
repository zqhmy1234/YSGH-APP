"""CI 预热 HF 模型：下载 BGE-M3 到缓存目录（失败即明确报错 + 写 annotation）

2026-08-26：CI #20 报 LocalEntryNotFoundError: outgoing traffic has been disabled——
强制 HF_HUB_OFFLINE=0（直接赋值，防 embedding.py/环境残留 setdefault 成 1），
并显式 local_files_only=False（防 sentence_transformers 默认按环境变量走离线）。
2026-08-27（批次 H2 R7）：模型路径 annotation——成功/失败都把 HF/模型路径以
workflow annotation 输出（GitHub 匿名 API 可读，排障不需登录日志）。
"""
import os
import sys
import traceback
from pathlib import Path

# 必须最先强制在线（直接赋值，覆盖任何残留/默认）
os.environ["HF_HUB_OFFLINE"] = "0"
os.environ["TRANSFORMERS_OFFLINE"] = "0"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# 仓库用到的模型清单（model id → 预期缓存/路径）；CI 失败时据此快速定位缺哪个模型
MODEL_PATHS: list[tuple[str, str, str]] = [
    ("BGE-M3 文本塔", "BAAI/bge-m3", "~/.cache/huggingface/hub/models--BAAI--bge-m3"),
    ("BGE-M3 sparse 投影", "BAAI/bge-m3 内文件", "sparse_linear.pt（BGE-M3 snapshots 下）"),
    ("Reranker 粗排", "backend/models/bge-reranker-base", "仓库本地模型（非 HF）"),
    ("SetFit 分类", "backend/models/setfit-classifier", "仓库本地模型（非 HF）"),
    ("SenseVoice(ONNX)", "funasr-onnx/modelscope", "~/.cache/modelscope 或 funasr 模型目录"),
]


def _ann(kind: str, title: str, msg: str) -> None:
    esc = msg.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    print(f"::{kind} title={title}::{esc}")
    print(msg, flush=True)


def err(msg):
    _ann("error", "warm-hf-models", msg)


def notice(msg):
    _ann("notice", "warm-hf-models", msg)


def annotate_model_paths() -> None:
    """把模型 → 路径映射写进 annotation（T2：匿名 API 可读，失败排障不用登录日志）"""
    hub = os.environ.get("HF_HUB_CACHE")
    if not hub:
        hf_home = os.environ.get("HF_HOME", str(Path.home() / ".cache" / "huggingface"))
        hub = str(Path(hf_home) / "hub")
    bge_dir = Path(hub) / "models--BAAI--bge-m3"
    lines = [
        f"HF_HUB_CACHE={hub}",
        f"BGE-M3 cached={'YES' if bge_dir.is_dir() else 'NO (将现场下载 ~2.2GB)'}",
    ]
    for name, model, path in MODEL_PATHS:
        lines.append(f"{name}: {model} → {path}")
    notice("模型路径 | " + " | ".join(lines))


try:
    annotate_model_paths()
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(4)
    print("下载/加载 BGE-M3 (BAAI/bge-m3, local_files_only=False)...", flush=True)
    model = SentenceTransformer(
        "BAAI/bge-m3",
        model_kwargs={"torch_dtype": torch.float16},
        local_files_only=False,
    )
    vec = model.encode(["预热测试"], normalize_embeddings=True, show_progress_bar=False)
    print(f"BGE-M3 预热完成, dim={len(vec[0])}", flush=True)
    from huggingface_hub import hf_hub_download

    p = hf_hub_download("BAAI/bge-m3", "sparse_linear.pt", local_files_only=False)
    print("sparse_linear.pt 就绪:", p, flush=True)
    notice("BGE-M3 预热完成（dim=" + str(len(vec[0])) + "）；sparse_linear.pt 就绪")
except Exception as exc:  # noqa: BLE001
    err(f"BGE-M3 预热失败: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}")
    sys.exit(1)
