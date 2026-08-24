"""bge-reranker 粗排（B2 双层 Rerank 第一层 · WP-F 2026-08-19）

本地 CrossEncoder（bge-reranker-base，中文），模型就绪自动启用；未就绪返回原序（不阻塞）。
模型路径：settings.reranker_model（默认 backend/models/bge-reranker-base）。
加载策略：懒加载 + 进程级单例（首次调用加载，失败记日志降级）。
"""
from __future__ import annotations

import logging
from functools import lru_cache

from app.core.config import settings

logger = logging.getLogger("yishu.rerank")


@lru_cache(maxsize=1)
def _load_model():
    from pathlib import Path

    from sentence_transformers import CrossEncoder

    # 审查修复(P1-10)：模型路径基于 __file__ 解析，不依赖 CWD
    backend_dir = Path(__file__).resolve().parent.parent.parent
    model_path = Path(settings.reranker_model or "bge-reranker-base")
    if not model_path.is_absolute():
        model_path = backend_dir / "models" / model_path
    if not (model_path / "config.json").exists():
        return None
    try:
        import torch

        # 2026-08-25 内存优化：fp16（bge-reranker fp32 ~1GB → fp16 ~0.5GB）
        # 修复（2026-08-25 RAG 审查）：sentence-transformers 3.4.1 的 CrossEncoder
        # 不接受 model_kwargs（旧版 API）→ 加载必抛 TypeError → 被 except 吞掉静默降级，
        # rerank 从未真正生效。3.x 用 automodel_args 透传 torch_dtype。
        return CrossEncoder(
            str(model_path),
            max_length=512,
            automodel_args={"torch_dtype": torch.float16},
        )
    except Exception as exc:  # noqa: BLE001 —— 模型加载失败降级为不重排
        logger.warning("reranker 加载失败，跳过重排: %s", exc)
        return None


def rerank(query: str, candidates: list[dict], top_k: int | None = None, min_score: float = 0.0) -> list[dict]:
    """重排候选（[{'id','text','score'}] → 同结构按相关性降序）

    候选无 text 或模型未就绪 → 原序返回（RRF 分数保底）。
    min_score > 0 时丢弃重排分数低于阈值的候选（RET-013 空结果引导/不误召回）。
    """
    if not candidates:
        return candidates
    model = _load_model()
    if model is None:
        return candidates
    pairs = [(query, c.get("text") or "") for c in candidates]
    if not any(t for _, t in pairs):
        return candidates
    try:
        scores = model.predict(pairs)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rerank 预测失败，跳过重排: %s", exc)
        return candidates
    scored = [(c, float(s)) for c, s in zip(candidates, scores, strict=True)]
    if min_score > 0:
        scored = [(c, s) for c, s in scored if s >= min_score]
    ranked = sorted(scored, key=lambda x: x[1], reverse=True)
    result = [c for c, _ in ranked]
    return result[:top_k] if top_k else result
