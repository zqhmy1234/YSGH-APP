"""阿里云百炼客户端（S1-03 外部 API 接入 · 决策 #2/#12）

覆盖三类能力（M1 依赖）：
  1. qwen-flash 文本：RAG 查询改写/路由/精排（B2）
  2. Qwen3-VL 图片塔：图片 → 语义描述 → 向量化（B2 图片检索）
  3. 护栏：内容安全审核（B5b，fail-safe：百炼不可用时默认拒发而非放行）

Mock 模式（MOCK_EXTERNAL_AI=true 或未配 key）：
  - 零费用、确定性输出，契约消费方可本地联调
  - 与真实响应同构（同字段），切真实 key 无代码改动
"""
from __future__ import annotations

import logging

from app.core.config import settings

logger = logging.getLogger("yishu.external")

# 模型名（百炼）
QWEN_FLASH = "qwen-flash"            # 文本：改写/路由/精排
QWEN_VL = "qwen3-vl-plus"            # 图片塔
QWEN_GUARD = "qwen-guard"            # 护栏（内容安全）

# 护栏判定：返回文本含此关键词视为"拦截"
_BLOCK_MARKERS = ("block", "refuse", "拒", "不合法", "违规", "有害")

_REWRITE_SYSTEM = (
    "你是记忆整理 App 的查询改写器。将用户的自然语言查询改写为适合向量检索的"
    "简洁中文查询（去掉口语、保留核心实体与意图），并识别时间表达。"
    "只输出改写后的查询，不要任何解释。"
)

_ROUTE_SYSTEM = (
    "你是查询路由器。判断查询意图，只输出一个词："
    "image（找照片/图片/截图）、text（找文字/笔记/碎片）、mixed（两者都要）。"
)

_GUARD_SYSTEM = (
    "你是内容安全审核员。判断输入是否含违法、色情、暴力、诈骗等违规内容。"
    "合规输出 PASS；违规输出 BLOCK。只输出一个词。"
)


def _llm_available() -> bool:
    """百炼可用判定：非 mock 且已配 key"""
    return not settings.mock_external_ai and bool(settings.dashscope_api_key)


def _chat_text(system: str, user: str, model: str = QWEN_FLASH) -> str:
    """调 qwen-flash 文本对话（同步），失败抛异常由调用方降级"""
    from dashscope import Generation

    resp = Generation.call(
        model=model,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        result_format="message",
    )
    if resp.status_code != 200:
        raise RuntimeError(f"dashscope {model} 调用失败: {resp.status_code} {resp.message}")
    return resp.output.choices[0].message.content


def rewrite_query(q: str) -> str:
    """LLM 查询改写（RAG B2；未配置时由调用方走规则兜底）"""
    if not _llm_available():
        raise RuntimeError("百炼未配置（MOCK 或缺 key），走规则兜底")
    return _chat_text(_REWRITE_SYSTEM, q).strip()


def route_query(q: str) -> str:
    """LLM 查询路由（image/text/mixed）"""
    if not _llm_available():
        raise RuntimeError("百炼未配置，走规则兜底")
    answer = _chat_text(_ROUTE_SYSTEM, q).strip().lower()
    for cand in ("image", "mixed", "text"):
        if cand in answer:
            return cand
    return "text"


def image_caption(image_path: str, prompt: str = "用一句话中文描述这张图片的内容和场景。") -> str:
    """Qwen3-VL 图片塔：图片 → 语义描述（供向量化索引/检索）"""
    if not _llm_available():
        raise RuntimeError("百炼未配置，图片塔不可用（需 DASHSCOPE_API_KEY）")
    from dashscope import MultiModalConversation

    resp = MultiModalConversation.call(
        model=QWEN_VL,
        messages=[
            {
                "role": "user",
                "content": [
                    {"image": f"file://{image_path}"},
                    {"text": prompt},
                ],
            }
        ],
    )
    if resp.status_code != 200:
        raise RuntimeError(f"dashscope {QWEN_VL} 调用失败: {resp.status_code} {resp.message}")
    parts = resp.output.choices[0].message.content
    return "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()


def moderate(text: str) -> dict:
    """内容安全护栏（B5b）：返回 {"pass": bool, "reason": str}

    fail-safe（决策 #12）：真实模式下百炼不可用/异常 → 默认拦截（拒发）；
    mock 模式 → 放行（联调用）。
    """
    if not _llm_available():
        logger.info("护栏 mock 放行（未配置百炼）")
        return {"pass": True, "reason": "mock"}
    try:
        answer = _chat_text(_GUARD_SYSTEM, text, model=QWEN_GUARD).strip().upper()
        blocked = any(m in answer for m in _BLOCK_MARKERS) or answer == "BLOCK"
        return {"pass": not blocked, "reason": "guard" if blocked else ""}
    except Exception as exc:  # noqa: BLE001 —— fail-safe：不可用即拦截
        logger.warning("护栏调用失败，fail-safe 拦截: %s", exc)
        return {"pass": False, "reason": "guard-unavailable"}
