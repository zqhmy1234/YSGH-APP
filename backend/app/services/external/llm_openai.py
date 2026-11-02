"""OpenAI 兼容的文本 LLM 客户端（2026-10-15 · 台账 §7.27）。

## 为什么有这条链

本项目的文本调用点（**内容护栏** / 查询改写 / 意图路由）原先只有一条路：百炼 DashScope
SDK（`external/dashscope.py`）。2026-10-15 实测踩到它的单点风险——机器上一把**过期的**
`DASHSCOPE_API_KEY`（用户级环境变量）顶掉了 `backend/.env` 里有效的 key，而护栏是
**fail-closed**（用户此前拍板的"默认拒发"CRITICAL 修复）⇒ **所有内容保存失败**
（用户侧看到"内容含敏感信息未保存"，App 完全不可用）。

故文本调用支持按 `LLM_PROVIDER=deepseek` 切到**任何 OpenAI 兼容端点**
（DeepSeek 官方 API 即 `POST {base_url}/chat/completions`）——同一份代码也能直连其它
兼容服务，不再被单一供应商的 key 可用性卡死。

## 与调用方的契约

与 `dashscope._chat_text` **完全一致**：成功返回文本、**失败抛异常**（调用点已有降级链）。
本函数自身**不做重试**——复用方 `dashscope._chat_text` 外层带 `with_retry`，重试只应有一处。

⚠️ 视觉调用（照片 caption）不经此处：DeepSeek 无视觉模型，恒走 `dashscope.image_caption`。
"""
from __future__ import annotations

import httpx


def chat_text(
    system: str,
    user: str,
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout: float = 30.0,
) -> str:
    """OpenAI 兼容 `chat/completions` 同步调用；非 200 或结构异常**抛 RuntimeError**。"""
    url = base_url.rstrip("/") + "/chat/completions"
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
        },
        timeout=timeout,
    )
    if resp.status_code != 200:
        # 不回显响应体全文（可能含账号信息）——截断即可定位
        raise RuntimeError(
            f"openai-compat {model} 调用失败: {resp.status_code} {resp.text[:200]}"
        )
    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        # 结构不对 ⇒ 明确报错（不返回空串：空串会让上游把"失败"当"模型说没违规"）
        raise RuntimeError(
            f"openai-compat {model} 响应结构异常: {type(exc).__name__}"
        ) from exc
