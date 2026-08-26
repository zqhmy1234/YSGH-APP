"""CI 预热 HF 模型：下载 BGE-M3 到缓存目录（失败即明确报错 + 写 annotation）

2026-08-26：CI #20 报 LocalEntryNotFoundError: outgoing traffic has been disabled——
强制 HF_HUB_OFFLINE=0（直接赋值，防 embedding.py/环境残留 setdefault 成 1），
并显式 local_files_only=False（防 sentence_transformers 默认按环境变量走离线）。
"""
import os
import sys
import traceback

# 必须最先强制在线（直接赋值，覆盖任何残留/默认）
os.environ["HF_HUB_OFFLINE"] = "0"
os.environ["TRANSFORMERS_OFFLINE"] = "0"
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def err(msg):
    msg = msg.replace("%", "%25").replace("\n", "%0A").replace("\r", "%0D")
    print(f"::error title=warm-hf-models::{msg}")
    print(msg, flush=True)


try:
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
except Exception as exc:  # noqa: BLE001
    err(f"BGE-M3 预热失败: {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}")
    sys.exit(1)
