"""SetFit 分类服务（M1 Part 3 · F2 文字碎片 5 类分类）

- 模型：backend/models/setfit-classifier（scripts/train_setfit.py 训练）
- 5 类：todo(待办)/idea(灵感)/emotion(情绪)/quote(引用)/mixed(混合)
- 三层裁决（B5-c）：本服务 = 第②层全局 SetFit；第①层个人规则（correction_log
  向量相似>0.8）与第③层共性回流微调由后续任务接入
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("yishu.classify")

_MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models" / "setfit-classifier"

DEFAULT_CLASSES = ["todo", "idea", "emotion", "quote", "mixed"]
DEFAULT_CLASSES_CN = ["待办", "灵感", "情绪", "引用", "混合"]


@lru_cache(maxsize=1)
def _load() -> tuple[object, list[str], list[str]]:
    """加载模型 + 标签映射（进程内单例）"""
    from setfit import SetFitModel

    model = SetFitModel.from_pretrained(_MODEL_DIR)
    labels_path = _MODEL_DIR / "labels.json"
    if labels_path.exists():
        meta = json.loads(labels_path.read_text(encoding="utf-8"))
        classes = meta["classes"]
        classes_cn = meta["classes_cn"]
    else:
        classes, classes_cn = DEFAULT_CLASSES, DEFAULT_CLASSES_CN
    return model, classes, classes_cn


def classify(text: str) -> dict:
    """单条分类 → {label, label_cn, confidence, scores}

    模型标签顺序 = 训练集 LabelEncoder 排序 = labels.json 的 classes 顺序。
    """
    if not text or not text.strip():
        raise ValueError("text 不能为空")
    model, classes, classes_cn = _load()
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
