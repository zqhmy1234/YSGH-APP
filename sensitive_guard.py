"""证件类敏感图片的本地判定（P0 隐私红线）。

原理：不上传任何数据，用本机 Chinese-CLIP 把图片向量与"证件类 / 普通类"
文字提示向量做相似度对比，零样本判断是否为证件类图片。

注意：CLIP 判定是概率性的，阈值（.env 的 SENSITIVE_THRESHOLD）需要用真实
证件样例校准；未命中不代表绝对安全，生产环境可叠加端侧 OCR 正则。
"""

import os

import numpy as np
from dotenv import load_dotenv

from clip_service import embed_text

load_dotenv()

THRESHOLD = float(os.getenv("SENSITIVE_THRESHOLD", "0.40"))
# 相对"普通类"最高分的差距要求：证件类必须显著高于普通类才拦截（降低误报）
MARGIN = float(os.getenv("SENSITIVE_MARGIN", "0.03"))

SENSITIVE_PROMPTS = [
    "中国居民身份证照片",
    "身份证正面",
    "护照个人信息页",
    "驾驶证照片",
    "军官证",
    "港澳通行证",
]

NORMAL_PROMPTS = [
    "书籍封面",
    "名片",
    "发票",
    "菜单",
    "会议纪要",
    "快递单",
    "风景照片",
    "聊天记录截图",
    "超市小票",
]

_prompt_vecs = None


def _get_prompt_vecs():
    """证件类 / 普通类提示向量（首次调用时生成并缓存，模型只加载一次）。"""
    global _prompt_vecs
    if _prompt_vecs is None:
        _prompt_vecs = (
            [embed_text(p) for p in SENSITIVE_PROMPTS],
            [embed_text(p) for p in NORMAL_PROMPTS],
        )
    return _prompt_vecs


def check_sensitive(image_vec: list) -> dict:
    """输入图片向量，返回判定结果。

    {
      "is_sensitive": bool,   # 判为证件类
      "score": float,         # 与最相似证件类提示的余弦相似度
      "matched": str | None,  # 命中的证件类提示
    }
    """
    s_vecs, n_vecs = _get_prompt_vecs()
    v = np.array(image_vec, dtype=float)
    v = v / (np.linalg.norm(v) + 1e-12)

    def _sims(vecs):
        a = np.array(vecs, dtype=float)
        a = a / (np.linalg.norm(a, axis=1, keepdims=True) + 1e-12)
        return a @ v

    s_scores = _sims(s_vecs)
    n_scores = _sims(n_vecs)
    s_max = float(s_scores.max())
    n_max = float(n_scores.max()) if len(n_scores) else 0.0
    matched = SENSITIVE_PROMPTS[int(s_scores.argmax())]
    is_sensitive = s_max >= THRESHOLD and s_max >= n_max + MARGIN
    return {
        "is_sensitive": bool(is_sensitive),
        "score": round(s_max, 4),
        "matched": matched if is_sensitive else None,
    }
