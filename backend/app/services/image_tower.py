"""图片塔适配层（R-4 · 2026-10-14）

**问题**：`image_vec`（以图搜图命名向量）里存的是 `BGE-M3(caption)` —— 即先把图片经
Qwen3-VL 转成一句 caption，再用**文本塔**编码。设计指定的图片塔是
`tongyi-embedding-vision-plus`（多模态 embedding），因账号未开通而一直走替代路径。

**本模块**：把「图片（或它的 caption）→ 向量」这件事收口到一处，使**账号开通后零改动切换**
（只改 `settings.image_tower_backend`）。

## 为什么切换不能只是换个函数（关键）

两种塔产出的是**互不相通的向量空间**：同一个 1024 维槽位里，caption 空间的向量与
vision 空间的向量**余弦分不可比**。若切换后新旧点混在一起检索，会**静默返回垃圾**
（不报错、不崩溃，只是排序全错）。所以本模块除出向量外，还提供**空间标识**
（`IMAGE_SPACE_*`），由 `vector_store` 写入 payload、检索端据此隔离。

维度依据（官方支持 64~1152，默认 1152，1024 在列）：
https://help.aliyun.com/zh/model-studio/multimodal-embedding-api-reference

## 依赖方向（务必保持，否则出循环 import）

    image_tower ──(函数内延迟 import)──> embedding / llm_ops
    vector_store ──(函数内延迟 import)──> image_tower      # 只取常量与 current_image_space()
    pipeline / rag.image / scripts ──> image_tower

**本模块绝不 import `vector_store` / `rag.*`**；`embedding`（torch 重）与 `llm_ops`
一律函数内延迟 import，避免把重依赖带上 `vector_store` 的 import 链。
"""
from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger("yishu.rag")


class ImageTowerError(RuntimeError):
    """图片塔不可用（未配 key / 模型未开通 / 输入不足）。"""


# ---- payload 隔离键与空间标识 ----
IMAGE_SPACE_KEY = "image_space"
IMAGE_SPACE_BGE = "bge_caption"  # 现状：BGE-M3(Qwen3-VL caption)
IMAGE_SPACE_VISION = "vision"  # tongyi-embedding-vision-plus（多模态）

_BACKEND_TO_SPACE = {
    "bge_caption": IMAGE_SPACE_BGE,
    "dashscope_vision": IMAGE_SPACE_VISION,
}


def current_image_space() -> str:
    """当前配置对应的向量空间标识（写入 payload / 检索过滤都用它）。

    实时读 `settings`（而非模块级缓存），这样测试 monkeypatch 与运行时切换都即时生效。
    """
    backend = settings.image_tower_backend
    try:
        return _BACKEND_TO_SPACE[backend]
    except KeyError:  # 理论上 pydantic Literal 已挡住，兜底不静默
        raise ImageTowerError(f"未知的图片塔后端：{backend!r}") from None


def encode_image_vector(
    *,
    image_path: str | None = None,
    caption: str | None = None,
) -> tuple[list[float], str]:
    """图片塔统一入口 —— 返回 `(向量, 空间标识)`。

    分支语义：

    | backend | 输入 | 结果 |
    |---|---|---|
    | `bge_caption` | 需要 `caption` | `BGE-M3(caption)`，空间 `bge_caption`（**与改动前逐字等价**）|
    | `dashscope_vision` | 有 `image_path` | 图片嵌入，空间 `vision` |
    | `dashscope_vision` | 无图、有 `caption` | **同一模型的文本输入**嵌入，空间 `vision` |

    ⚠️ 最后一行是**同空间降级**，不是跨空间混入：视觉 model 本身是图文联合嵌入，
    caption 走文本通道落进同一空间。之所以降级而不报错：生产 `_index_after_commit`
    执行时图片临时文件已被 `pipeline_photo.py` 删掉，只剩 caption —— 报错会让该照片
    **永久不可搜图**，而降级至少能填上槽位。

    只有「既无图又无 caption」才 `raise ImageTowerError`，由调用方既有的
    try/except 记 warning 跳过（照片仍可被 `text_vec` 搜到）。
    """
    backend = settings.image_tower_backend

    if backend == "bge_caption":
        if not caption:
            raise ImageTowerError("bge_caption 后端需要 caption（无 caption 无法出向量）")
        from app.services.embedding import encode_dense  # 延迟 import：torch 重

        return encode_dense([caption])[0], IMAGE_SPACE_BGE

    if backend == "dashscope_vision":
        from app.services.llm_ops import embed_image, embed_text_in_vision  # 延迟 import

        dim = settings.image_tower_dimension
        if image_path:
            return embed_image(image_path, dimension=dim), IMAGE_SPACE_VISION
        if caption:
            logger.info("图片塔未拿到图片文件，降级用 caption 文本嵌入（同空间）")
            return embed_text_in_vision(caption, dimension=dim), IMAGE_SPACE_VISION
        raise ImageTowerError("dashscope_vision 后端需要 image_path 或 caption，二者皆无")

    raise ImageTowerError(f"未知的图片塔后端：{backend!r}")  # pragma: no cover —— Literal 已挡
