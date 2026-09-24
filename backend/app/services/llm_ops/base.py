"""base.py —— llm_ops 底层转发（dashscope 访问收敛在 base 与 moderate 两处）

Wave 0 建立。后续若需要新底层能力（如流式/新模型），由集成 Agent 评估后
在此增加转发函数，各域 Agent 不得直接 import dashscope。
例外（重构 P0-1 解环，2026-08-27）：llm_ops/moderate.py 策略选择器为消除
base ⇄ guard_managed 模块环获准直接依赖 dashscope（chat 兜底），
其余模块仍遵守"不直接 import dashscope"约定。
"""
from __future__ import annotations

from typing import Any

from app.services.external import dashscope
from app.services.llm_ops.moderate import moderate as _moderate_selector


def llm_available() -> bool:
    """LLM 是否可用（未配 key / mock 模式 → False）"""
    return dashscope._llm_available()  # noqa: SLF001 —— 同仓转发，接受私有访问


def chat_text(system: str, user: str, model: str = dashscope.QWEN_FLASH) -> str:
    """统一文本对话入口（改写/精排/归并/标注/护栏复用）"""
    return dashscope._chat_text(system, user, model)  # noqa: SLF001


def rewrite_query(q: str) -> str:
    return dashscope.rewrite_query(q)


def image_caption(image_path: str, prompt: str | None = None) -> str:
    """VL 图片塔（Qwen3-VL）转发 —— B9b：rag 不再直连 external.dashscope。

    `prompt=None` 时不传第二参，**沿用 dashscope 的默认提示词**（避免在此重复字面量而漂移）。
    转发用**属性访问**（`dashscope.image_caption`）⇒ 测试对 dashscope 模块的打桩仍生效
    （test_image_search / test_pipeline / test_ba3_ai_chain 均如此打桩）。
    """
    if prompt is None:
        return dashscope.image_caption(image_path)
    return dashscope.image_caption(image_path, prompt)


def route_query(q: str) -> str:
    return dashscope.route_query(q)


def moderate(text: str) -> dict[str, Any]:
    """护栏检测（Wave2-F 2026-08-26 起：百炼托管优先、chat 兜底）

    策略实现已收敛至 llm_ops/moderate.py（托管优先、chat 兜底、mock 模式直通），
    base 仅保留入口转发（重构 P0-1 解环：不再 import guard_managed）。
    返回 {"pass": bool, "reason": str, ...}，签名与返回值不变。
    """
    return _moderate_selector(text)
