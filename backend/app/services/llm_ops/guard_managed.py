"""guard_managed.py —— B5b 域：百炼托管护栏（qwen_response_check）

任务归属：Wave 2 Agent F（M1 补遗域）独占本文件（2026-08-26 新建）。

背景（audit_B5b #1）：dashscope.py 冻结只读，其 moderate() 为"规则预检 +
qwen-flash chat 模拟护栏"（无托管）。B5b 定稿为百炼托管护栏——通过
X-DashScope-DataInspection header 开启服务端内容安全检测（qwen_response_check）。
本模块用 httpx 直发（不 import dashscope），实现托管检测 + "托管优先、chat 兜底"
策略入口，供 llm_ops.base.moderate 接线。

接口：
- qwen_response_check(text, ...) -> dict：托管检测单次调用（httpx 直发）。
  返回 {"pass": bool, "reason": str, "detector": "managed", ...}。
  不可用（mock / 无 key）抛 RuntimeError（调用方走 chat 兜底，保持现有契约）。
- moderate_managed(text) -> dict：托管优先、chat 兜底 策略入口
  （托管可用 → 托管判定；托管异常/不可用 → llm_ops.base.moderate 的 chat 规则兜底）。

fail-safe 对齐（决策 #12）：托管检测命中（含服务端审查拦截）→ 拒发；
托管网络异常 → 交给 chat 兜底（dashscope.moderate 在生产无 key 时已 fail-closed）。
"""
from __future__ import annotations

import json
import logging

import httpx

from app.core.config import settings

logger = logging.getLogger("yishu.guard_managed")

# 托管检测端点（百炼文本生成；X-DashScope-DataInspection 自动审查输入/输出）
_DEFAULT_GENERATION_URL = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
# 托管模型：qwen-flash（与 dashscope.QWEN_FLASH 对齐；不 import dashscope，避免冻结文件耦合）
_MANAGED_MODEL = "qwen-flash"
_TIMEOUT_SECONDS = 15.0

# 审查判定词（LLM 输出含任一即视为命中违规；与 dashscope._BLOCK_MARKERS 同语义）
_BLOCK_MARKERS = ("block", "refuse", "拒", "不合法", "违规", "有害")

# 服务端数据审查拦截特征（DashScope 返回非 200 时的错误体关键词）
# 注意：不用裸 "inspection"（"no inspection" 等否定/无关上下文会误判）
_INSPECTION_ERROR_HINTS = ("datainspection", "data_inspection", "content_filter", "violate", "违规", "审查", "拦截")


def _managed_available() -> bool:
    """托管护栏可用判定：非 mock 且已配 key（与 dashscope._llm_available 同口径）"""
    return not settings.mock_external_ai and bool(settings.dashscope_api_key)


def _build_headers() -> dict[str, str]:
    """托管请求头：Authorization + X-DashScope-DataInspection（+ workspace 头）"""
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key}",
        "Content-Type": "application/json",
        "X-DashScope-DataInspection": "enable",
    }
    if settings.dashscope_workspace_id:
        headers["X-DashScope-WorkSpace"] = settings.dashscope_workspace_id
    return headers


def _is_inspection_block(status_code: int, body: str) -> bool:
    """判断非 200 响应是否为服务端审查拦截（而非普通 API 错误）"""
    if status_code not in (400, 403):
        return False
    lowered = (body or "").lower()
    return any(h in lowered for h in _INSPECTION_ERROR_HINTS)


def qwen_response_check(
    text: str,
    *,
    timeout: float = _TIMEOUT_SECONDS,
    max_retries: int = 2,
) -> dict:
    """百炼托管护栏单次调用：X-DashScope-DataInspection 直发，不碰 dashscope.py

    text：待检测文本（用户输入或待发内容）。
    返回 {"pass": bool, "reason": str, "detector": "managed", "detail": str}：
      pass=True  → 未命中（放行）
      pass=False → 命中违规（拒发），reason=managed-block / managed-inspection
    不可用（mock / 无 key）→ 抛 RuntimeError（调用方走 chat 兜底）。
    网络/服务异常（非审查拦截）→ 抛 RuntimeError（兜底决策，不在此 fail-closed）。
    """
    if not _managed_available():
        raise RuntimeError("百炼未配置（MOCK 或缺 key），托管护栏不可用")
    body = {
        "model": _MANAGED_MODEL,
        "input": {
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是内容安全审核员。判断输入是否含违法、色情、暴力、诈骗等违规内容。"
                        "合规输出 PASS；违规输出 BLOCK。只输出一个词。"
                    ),
                },
                {"role": "user", "content": text},
            ]
        },
        "parameters": {"result_format": "message", "max_tokens": 32, "temperature": 0},
    }
    url = settings.dashscope_base_url or _DEFAULT_GENERATION_URL
    last_exc: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            resp = httpx.post(url, headers=_build_headers(), json=body, timeout=timeout)
        except httpx.HTTPError as exc:
            last_exc = exc
            logger.warning("托管护栏网络异常（第 %d 次）: %s", attempt + 1, exc)
            continue
        if resp.status_code != 200:
            if _is_inspection_block(resp.status_code, resp.text):
                return {
                    "pass": False,
                    "reason": "managed-inspection",
                    "detector": "managed",
                    "detail": f"服务端内容审查拦截（HTTP {resp.status_code}）",
                }
            last_exc = RuntimeError(f"托管护栏 HTTP {resp.status_code}: {resp.text[:200]}")
            continue
        try:
            data = resp.json()
            content = (
                data.get("output", {}).get("choices", [{}])[0].get("message", {}).get("content", "")
            )
        except (json.JSONDecodeError, IndexError, AttributeError, KeyError) as exc:
            last_exc = RuntimeError(f"托管护栏响应解析失败: {exc}")
            continue
        answer = (content or "").strip().upper()
        blocked = any(m in answer for m in _BLOCK_MARKERS) or answer == "BLOCK"
        return {
            "pass": not blocked,
            "reason": "managed-block" if blocked else "managed-ok",
            "detector": "managed",
            "detail": answer[:80] or "（空响应）",
        }
    raise RuntimeError(f"托管护栏调用失败（{max_retries + 1} 次尝试）: {last_exc}")


def moderate_managed(text: str) -> dict:
    """托管优先、chat 兜底 策略入口（B5b-1 定稿接线点）

    1. 托管可用 → qwen_response_check（pass/reject 由托管判定）；
    2. 托管不可用/异常 → 回退 llm_ops.base.moderate（规则预检 + qwen-flash chat 双保险，
       生产无 key 时 fail-closed 拒发）。
    返回结构兼容 base.moderate：{"pass": bool, "reason": str, ...}。
    """
    try:
        return qwen_response_check(text)
    except RuntimeError as exc:
        logger.info("托管护栏不可用，chat 兜底: %s", exc)
        from app.services.llm_ops.base import moderate as _chat_moderate

        return _chat_moderate(text)
