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
from pathlib import Path

# 限制线程数（必须在 torch import 前设置）
os.environ.setdefault("OMP_NUM_THREADS", "4")
os.environ.setdefault("TORCH_NUM_THREADS", "4")
# 离线加载（模型已本地缓存）：sentence_transformers 加载前会 HEAD huggingface.co 校验，
# 国内网络 connect 超时 → 每次测试/启动挂 2 分钟+（实测 10s×5 重试退避）。
# 缓存完整时离线加载零差异；CI/新机器需下载时显式设 HF_HUB_OFFLINE=0（见 .github/workflows/ci.yml）。
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import torch  # noqa: E402
from sentence_transformers import SentenceTransformer  # noqa: E402

logger = logging.getLogger("yishu.rag")

# BGE-M3 的 HF repo id（本地缺模型时的回退目标）
MODEL_REPO = "BAAI/bge-m3"
# 本地模型目录（backend/models/bge-m3，.gitignore 已排除 ⇒ 不入库）。
# 为什么优先本地：① 离线可用，不受 huggingface.co / hf-mirror 可达性影响
# （本机实测两者 SSL 均被中断，2026-10-04）；② 免掉每次启动的 HEAD 校验超时。
# 获取方式见 backend/models/README.md（ModelScope 源，国内可达）。
_LOCAL_DIR = Path(__file__).resolve().parents[2] / "models" / "bge-m3"
MODEL_NAME = str(_LOCAL_DIR) if (_LOCAL_DIR / "config.json").is_file() else MODEL_REPO

torch.set_num_threads(4)


@lru_cache(maxsize=1)
def get_model() -> SentenceTransformer:
    """加载 BGE-M3（fp16，~1.2GB 内存；单例缓存）

    模型来源二选一（按`MODEL_NAME` 自动判定，无需改配置）：
      · 本地目录 `backend/models/bge-m3/config.json` 存在 → 用它（离线，推荐）
      · 否则回退 HF repo id `BAAI/bge-m3`（需HF_HUB_OFFLINE=0 且网络可达）
    """
    logger.info("加载 BGE-M3 模型: %s", MODEL_NAME)
    return SentenceTransformer(MODEL_NAME, model_kwargs={"torch_dtype": torch.float16})


def _remote_base() -> str:
    """共享 embedding 服务的地址（未配置则返回空 = 走本地模型）。

    2026-10-31：压测证明「每进程各加载一份模型」是吞吐瓶颈——
    4 个 worker 时日志出现 `os error 1455`（页面文件不足），模型加载失败、
    反而比单 worker 更慢（0.22 → 0.11 条/秒）。配了本地址后本进程**完全不加载模型**，
    内存从 ~1.2GB 降到几十 MB，worker 数量就能线性扩。
    """
    from app.core.config import settings

    return str(getattr(settings, "embedding_service_url", "") or "")


def _fallback_allowed() -> bool:
    """共享服务失败时是否允许回退本地模型（默认不允许，见 config 注释）。"""
    from app.core.config import settings

    return bool(getattr(settings, "embedding_fallback_local", False))


def _remote_encode(texts: list[str], want: tuple[str, ...]) -> dict:
    """调共享服务；失败由调用方决定是否回退本地。"""
    import httpx

    base = _remote_base().rstrip("/")
    resp = httpx.post(
        f"{base}/embed",
        json={"texts": texts, "want": list(want)},
        timeout=60.0,
    )
    resp.raise_for_status()
    return resp.json()


def encode_dense(texts: list[str], normalize: bool = True) -> list[list[float]]:
    """dense 向量（BGE-M3 默认池化，1024 维）

    配了 `EMBEDDING_SERVICE_URL` 就走共享服务（本进程不加载模型）；
    调用失败**回退本地**——宁可慢一点，也不能让整条管线因为一个边车服务挂掉。
    """
    base = _remote_base()
    if base:
        try:
            return _remote_encode(texts, ("dense",))["dense"]
        except Exception as exc:  # noqa: BLE001
            if not _fallback_allowed():
                # 2026-10-31：默认**不回退**。回退会把 1.2GB 模型加载进本进程，
                # 挤爆内存后连带 backend/worker 一起挂 —— 宁可这一条失败（RQ 会重试），
                # 也不能让整台机器陪葬。
                logger.error("共享 embedding 服务不可用且已禁用本地回退: %s", exc)
                raise
            logger.warning("共享 embedding 服务不可用，回退本地模型: %s", exc)
    model = get_model()
    vecs = model.encode(texts, normalize_embeddings=normalize, show_progress_bar=False)
    return [v.tolist() for v in vecs]


@lru_cache(maxsize=1)
def _get_sparse_linear() -> torch.nn.Linear:
    """BGE-M3 sparse 投影层（sparse_linear.pt：Linear(1024→1)）

    sentence-transformers 3.x 移除了 SparseEmbedding 模块，但模型快照仍带
    sparse_linear.pt —— 手动加载，按官方公式 sparse = relu(linear(token_emb))。

    本地目录模式下直接读文件（`hf_hub_download` 只认 HF 缓存/在线 repo，
    对`backend/models/bge-m3` 这种本地路径无效）。
    """
    local_pt = _LOCAL_DIR / "sparse_linear.pt"
    if local_pt.is_file():
        path = str(local_pt)
    else:
        from huggingface_hub import hf_hub_download

        path = hf_hub_download(MODEL_REPO, "sparse_linear.pt", local_files_only=True)
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
    base = _remote_base()
    if base:
        try:
            return _remote_encode(texts, ("sparse",))["sparse"]
        except Exception as exc:  # noqa: BLE001
            if not _fallback_allowed():
                logger.error("共享 embedding 服务不可用（sparse）且已禁用本地回退: %s", exc)
                raise
            logger.warning("共享 embedding 服务不可用（sparse），回退本地模型: %s", exc)
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
