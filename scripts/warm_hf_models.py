"""CI 预热 HF 模型：下载 BGE-M3 到缓存目录（失败即明确报错 + 写 annotation）"""
import os
import sys

os.environ.setdefault("HF_HUB_OFFLINE", "0")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def err(msg):
    # GitHub Actions workflow command：写进 annotation（API 匿名可读）
    msg = msg.replace("%", "%25").replace("\n", "%0A").replace("\r", "%0D")
    print(f"::error title=warm-hf-models::{msg}")
    print(msg, flush=True)


try:
    import torch
    from sentence_transformers import SentenceTransformer

    torch.set_num_threads(4)
    print("下载/加载 BGE-M3 (BAAI/bge-m3)...", flush=True)
    model = SentenceTransformer("BAAI/bge-m3", model_kwargs={"torch_dtype": torch.float16})
    vec = model.encode(["预热测试"], normalize_embeddings=True, show_progress_bar=False)
    print(f"BGE-M3 预热完成, dim={len(vec[0])}", flush=True)
    from huggingface_hub import hf_hub_download

    p = hf_hub_download("BAAI/bge-m3", "sparse_linear.pt", local_files_only=True)
    print("sparse_linear.pt 就绪:", p, flush=True)
except Exception as exc:  # noqa: BLE001
    err(f"BGE-M3 预热失败: {type(exc).__name__}: {exc}")
    sys.exit(1)
