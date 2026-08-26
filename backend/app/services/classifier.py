"""SetFit 分类服务（M1 Part 3 · F2 文字碎片 5 类分类）

- 模型：backend/models/setfit-classifier（scripts/train_setfit.py 训练）
- 5 类：todo(待办)/idea(灵感)/emotion(情绪)/quote(引用)/mixed(混合)
- 三层裁决（B5-c）：本服务 = 第②层全局 SetFit；第①层个人规则（correction_log
  向量相似>0.8）与第③层共性回流微调由后续任务接入
"""
from __future__ import annotations

import os

# 离线加载（模型已本地缓存）：setfit→transformers→huggingface_hub 首次使用会 HEAD
# huggingface.co 校验（本机不可达，10s×5 重试 × 多文件 = 卡死 2min+）。
# 必须与 embedding.py 同模式设置；漏设会导致 worker 里 classifier 先于 embedding
# 加载时联网卡死（2026-08-20 E2E 实测复现）。
os.environ.setdefault("HF_HUB_OFFLINE", "1")

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("yishu.classify")

_MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models" / "setfit-classifier"

DEFAULT_CLASSES = ["todo", "idea", "emotion", "quote", "mixed"]
DEFAULT_CLASSES_CN = ["待办", "灵感", "情绪", "引用", "混合"]

# 标签权威词表（审查 P1-09 收敛：corrections/correction 模块引用，消除三份重复定义）
VALID_CLASSES = set(DEFAULT_CLASSES)
LABEL_CN_MAP = dict(zip(DEFAULT_CLASSES, DEFAULT_CLASSES_CN, strict=True))


@lru_cache(maxsize=1)
def _load() -> tuple[object | None, list[str], list[str]]:
    """加载模型 + 标签映射（进程内单例）

    2026-08-25 内存优化：model_kwargs torch_dtype=float16（SetFit 底座=BGE-M3 全参微调，
    fp32 实测 2.2GB → fp16 约 1.2GB；CPU 推理可用，test_setfit 回归覆盖）。
    2026-08-26 降级：模型目录缺失/加载失败（CI 全新检出无 gitignore 权重、生产未预置）
    → 返回 None，classify/classify_batch 走确定性降级（mixed），不崩溃（与 rerank/caption 降级同模式）。
    """
    try:
        import torch
        from setfit import SetFitModel

        model = SetFitModel.from_pretrained(_MODEL_DIR, model_kwargs={"torch_dtype": torch.float16})
    except Exception as exc:  # noqa: BLE001 —— 模型不可用降级（不阻断分类/纠错链路）
        logger.warning("SetFit 模型不可用（%s），分类降级为 mixed 规则结果: %s", _MODEL_DIR, exc)
        return None, DEFAULT_CLASSES, DEFAULT_CLASSES_CN
    labels_path = _MODEL_DIR / "labels.json"
    if labels_path.exists():
        meta = json.loads(labels_path.read_text(encoding="utf-8"))
        classes = meta["classes"]
        classes_cn = meta["classes_cn"]
    else:
        classes, classes_cn = DEFAULT_CLASSES, DEFAULT_CLASSES_CN
    return model, classes, classes_cn


def _fallback_result(text: str, classes: list[str], classes_cn: list[str]) -> dict:
    """模型不可用时确定性降级：mixed（不猜具体类，避免错误分类污染纠错/画像）"""
    return {
        "label": "mixed",
        "label_cn": "混合",
        "confidence": 0.0,
        "scores": [{"label": c, "label_cn": cn, "score": 0.0} for c, cn in zip(classes, classes_cn, strict=True)],
    }


def classify(text: str) -> dict:
    """单条分类 → {label, label_cn, confidence, scores}

    模型标签顺序 = 训练集 LabelEncoder 排序 = labels.json 的 classes 顺序。
    """
    if not text or not text.strip():
        raise ValueError("text 不能为空")
    model, classes, classes_cn = _load()
    if model is None:
        return _fallback_result(text, classes, classes_cn)
    probs = model.predict_proba([text])[0]
    idx = int(probs.argmax())
    return {
        "label": classes[idx],
        "label_cn": classes_cn[idx],
        "confidence": round(float(probs[idx]), 4),
        "scores": [
            {"label": c, "label_cn": cn, "score": round(float(p), 4)}
            for c, cn, p in zip(classes, classes_cn, probs, strict=True)
        ],
    }


def classify_batch(texts: list[str]) -> list[dict]:
    """批量分类（worker 攒批用；单次推理摊薄模型加载成本）

    CPU 实测：单条 predict ~27s（2.2GB SetFit 无 GPU），批 10 条约 30s——
    必须攒批，禁止逐条调 classify（2026-08-20 实测记录）。
    """
    if not texts:
        return []
    model, classes, classes_cn = _load()
    if model is None:
        return [_fallback_result(t, classes, classes_cn) for t in texts]
    probs = model.predict_proba(list(texts))
    results = []
    for p in probs:
        idx = int(p.argmax())
        results.append({
            "label": classes[idx],
            "label_cn": classes_cn[idx],
            "confidence": round(float(p[idx]), 4),
            "scores": [
                {"label": c, "label_cn": cn, "score": round(float(s), 4)}
                for c, cn, s in zip(classes, classes_cn, p, strict=True)
            ],
        })
    return results


def classify_job(text: str) -> dict:
    """RQ 任务：SetFit 分类（P2-01 推理移 worker——worker 进程执行，API 只入队）

    单条 predict ~27s（CPU 实测），放 API 进程会占满线程池；入队后由
    worker 消费，客户端经 GET /classify/jobs/{id} 轮询结果。
    """
    return classify(text)
