"""BGE-M3 文本塔（B2 设计：dense + sparse 三合一）

- dense 向量：1024 维（fp16 加载省内存）
- sparse 向量：lexical 权重（关键词精确匹配，中文描述性搜索刚需）
- 模型缓存为模块级单例（worker 进程内只加载一次）
- ⚠️ 限制 torch 线程数：CPU 版默认开满核 → 内存峰值超限（16GB 机器实测 OOM）
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache

# 限制线程数（必须在 torch import 前设置）
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("TORCH_NUM_THREADS", "4")

import torch  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

logger = logging.getLogger("yishu.rag")

MODEL_NAME = "BAAI/bge-m3"


torch.set_num_threads(4)


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    """加载 BGE-M3（fp16，~1.2GB 内存；单例缓存）"""
    logger.info("加载 BGE-M3 模型...")
    return SentenceTransformer(MODEL_NAME, model_kwargs={"torch_dtype": torch.float16})


def encode_dense(texts: list[str], normalize: bool = True) -> list[list[float]]:
    """dense 向量（BGE-M3 默认池化，1024 维）"""
    model = get_model()
    vecs = model.encode(texts, normalize_embeddings=normalize, show_progress_bar=False)
    return [v.tolist() for v in vecs]


@lru_cache(maxsize=1)
def _get_sparse_linear() -> torch.nn.Linear:
    """BGE-M3 sparse 投影层（sparse_linear.pt：Linear(1024→1)）

    sentence-transformers 3.x 移除了 SparseEmbedding 模块，但模型快照仍带
    sparse_linear.pt —— 手动加载，按官方公式 sparse = relu(linear(token_emb))。
    """
    from huggingface_hub import hf_hub_download

    path = hf_hub_download(MODEL_NAME, "sparse_linear.pt", local_files_only=True)
    state = torch.load(path, map_location="cpu", weights_only=True)
    lin = torch.nn.Linear(1024, 1)
    lin.load_state_dict(state)
    lin.eval()
    return lin


def encode_sparse(texts: list[str]) -> list[dict[str, float]]:
    """sparse 向量（lexical weights：token_id→权重，Qdrant sparse 格式）

    BGE-M3 官方 sparse 公式：weights = relu(sparse_linear(last_hidden_state))，
    索引 = token id（Qdrant sparse 向量索引无词汇表限制，直接用 token id）。
    """
    model = get_model()
    sparse_linear = _get_sparse_linear()
    features = model.tokenize(texts)
    with torch.no_grad():
        out = model[0](features)
    token_emb = out["token_embeddings"]  # [B, L, 1024]
    weights = torch.relu(sparse_linear(token_emb.float())).squeeze(-1)  # [B, L]（稀疏投影按 fp32 保精度）
    input_ids = out["input_ids"]  # [B, L]

    result: list[dict[str, float]] = []
    for i in range(len(texts)):
        nz = torch.nonzero(weights[i], as_tuple=False).squeeze(-1)
        if nz.numel() == 0:
            result.append({})
            continue
        indices = [int(x) for x in input_ids[i][nz].tolist()]
        values = [float(x) for x in weights[i][nz].tolist()]
        result.append(dict(zip(indices, values, strict=True)))
    return result


def encode_query(text: str) -> tuple[list[float], dict[str, float]]:
    """查询编码：dense + sparse 一并返回（检索 API 用）"""
    dense = encode_dense([text])[0]
    sparse = encode_sparse([text])[0]
    return dense, sparse
