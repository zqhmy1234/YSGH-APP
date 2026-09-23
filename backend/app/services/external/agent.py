"""Agent 服务适配器（AG5）：本后端 → Agent 服务（独立进程）的**唯一出口**。

设计要点（每条都有理由，改动前请先读）：
  1. **user_id 只由本后端注入**：agent 服务本身不鉴权用户身份（见 `agent/UPSTREAM.md` 五-1），
     故它**不得暴露公网**；本后端是唯一入口，JWT → user.id 在此处落为可信 `user_id`。
  2. **刻意不套 `with_retry`**：该装饰器对 5xx/超时重试，而对话调用**非幂等**——
     agent 会经 `save_memory` 等工具写库，超时重试会造成**重复落库**。
     故单次尝试 + 明确失败（宁可报错让人重问，也不产生脏数据）。
  3. **不伪造回复**：任何失败路径都抛异常，绝不在本地生成"看似 AI 的回答"
     （本项目铁律：假状态比错误更贵）。
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

from app.core.config import settings

logger = logging.getLogger("yishu.external.agent")


class AgentServiceError(RuntimeError):
    """Agent 服务不可用/返回异常。

    `kind`：unavailable（连不上/超时）| http_error（非 2xx）| bad_payload（响应不可解析）
    `http_status`：建议映射给客户端的状态码（502/504）
    """

    def __init__(self, message: str, kind: str, http_status: int) -> None:
        super().__init__(message)
        self.kind = kind
        self.http_status = http_status


@dataclass(frozen=True)
class AgentChatResult:
    text: str
    latency_ms: int
    raw: dict


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings.agent_service_token:
        # 与 agent 服务的共享密钥（对应其 AGENT_SERVICE_TOKEN）；未配置则不带（本地开发）
        headers["X-Agent-Token"] = settings.agent_service_token
    return headers


def call_agent_chat(user_id: str, message: str, thread_id: str) -> AgentChatResult:
    """调 agent 服务跑一轮对话，返回其回复文本。

    `thread_id` 用作 agent 侧的会话键（上游语义：同一 thread_id 保多轮上下文）。
    本后端传 **conversation_id**（而非 user_id）——一个用户可有多个会话，
    且上游 HTTP 面曾把 thread_id 设成 run_id 导致"每次对话都失忆"（见 UPSTREAM 五-2）。
    """
    url = f"{settings.agent_service_base_url.rstrip('/')}/v1/chat"
    payload = {"user_id": user_id, "message": message, "thread_id": thread_id}
    started = time.monotonic()
    try:
        with httpx.Client(timeout=settings.agent_service_timeout_s) as client:
            resp = client.post(url, json=payload, headers=_headers())
    except httpx.TimeoutException as exc:
        raise AgentServiceError(
            f"Agent 服务响应超时（{settings.agent_service_timeout_s:.0f}s）", "unavailable", 504
        ) from exc
    except httpx.HTTPError as exc:
        raise AgentServiceError(f"Agent 服务连接失败：{exc}", "unavailable", 502) from exc

    latency_ms = int((time.monotonic() - started) * 1000)
    if resp.status_code >= 400:
        raise AgentServiceError(
            f"Agent 服务返回 {resp.status_code}：{resp.text[:200]}", "http_error", 502
        )
    try:
        body = resp.json()
    except ValueError as exc:
        raise AgentServiceError("Agent 服务响应不可解析为 JSON", "bad_payload", 502) from exc

    reply = body.get("reply")
    if not isinstance(reply, str) or not reply.strip():
        raise AgentServiceError("Agent 服务未返回有效 reply 文本", "bad_payload", 502)
    logger.info("agent 调用完成 user=%s thread=%s latency_ms=%d", user_id, thread_id, latency_ms)
    return AgentChatResult(text=reply, latency_ms=latency_ms, raw=body)


def health() -> dict:
    """探活（供运维/门禁用；失败不抛，返回状态字典）。"""
    url = f"{settings.agent_service_base_url.rstrip('/')}/health"
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.get(url)
        return {"reachable": resp.status_code == 200, "status": resp.status_code,
                "body": resp.json() if resp.status_code == 200 else resp.text[:200]}
    except httpx.HTTPError as exc:
        return {"reachable": False, "status": None, "body": str(exc)}
