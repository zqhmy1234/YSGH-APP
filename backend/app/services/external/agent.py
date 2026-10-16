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

import json
import logging
import time
from collections.abc import Iterator
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
    # 结构化附加（cards/chips，2026-10-09）：agent 从 `save_memory` 工具结果派生，
    # 本后端据此产出 `kind=cards` 与 chips。缺省空 dict ⇒ 非记忆类对话行为不变。
    meta: dict | None = None


def _headers() -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    if settings.agent_service_token:
        # 与 agent 服务的共享密钥（对应其 AGENT_SERVICE_TOKEN）；未配置则不带（本地开发）
        headers["X-Agent-Token"] = settings.agent_service_token
    return headers


def call_agent_chat(
    user_id: str, message: str, conversation_id: str, attachments: list[dict] | None = None
) -> AgentChatResult:
    """调 agent 服务跑一轮对话，返回其回复文本。

    `conversation_id` 用作 agent 侧的会话键素材——一个用户可有多个会话，
    且上游 HTTP 面曾把 thread_id 设成 run_id 导致"每次对话都失忆"（见 UPSTREAM 五-2）。

    ⚠️ thread_id 派生规则（2026-10-08 服务间实联调修复）：agent 侧有**归属校验**
    （`agent/src/yishu/security.py::validate_thread_id`）——thread_id 必须等于
    user_id 或以 `{user_id}:` 开头，否则 403「thread_id 不属于当前用户」。
    本后端此前直接传 conversation_id（独立 uuid）⇒ 首次真实联调即被拒。
    按 `f"{user_id}:{conversation_id}"` 派生后：① 满足派生规则天然过校验；
    ② 不同用户的相同 conversation uuid 也绝不共享上下文（派生串含 user_id）；
    ③ 同一会话派生串稳定 ⇒ 多轮上下文保持。本链路此前从未真实跑通过，
    无存量 thread 数据，改派生规则零迁移成本。
    """
    thread_id = f"{user_id}:{conversation_id}"
    url = f"{settings.agent_service_base_url.rstrip('/')}/v1/chat"
    payload = {"user_id": user_id, "message": message, "thread_id": thread_id}
    # 2026-10-16（附件契约最小可用）：附件**结构化透传**（agent 侧仅留痕/透传，不解析内容）。
    # 有值才带：`attachments=None/[]` 时 payload 与本函数既有形状**逐字相同**——既有调用方、
    # 既有桩测试（3 参 fake）行为零变化。附件真正被 AI 读到的是 `message` 里的文本提示
    # （由 api/chat.py `_attachment_hint` 组装），不依赖 agent 侧实现细节。
    if attachments:
        payload["attachments"] = attachments
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
    meta = body.get("meta")
    logger.info("agent 调用完成 user=%s thread=%s latency_ms=%d", user_id, thread_id, latency_ms)
    return AgentChatResult(
        text=reply,
        latency_ms=latency_ms,
        raw=body,
        meta=meta if isinstance(meta, dict) else None,
    )


def _decode_sse_data(event: str, raw: str) -> tuple[str, dict]:
    """解析一条 SSE 帧的 data（JSON）。畸形/空 data 一律给 `{}`——**不抛**。

    为什么不抛：SSE 帧的破损（中间层改写、半包）不该把整轮对话打成 500；
    调用方按"这个 kind 没有可用字段"处理即可（如 delta 取不到 text 就当空串跳过）。
    """
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        return event, {}
    return event, data if isinstance(data, dict) else {}


def _iter_sse(resp: httpx.Response) -> Iterator[tuple[str, dict]]:
    """极简 SSE 解析：`event:` + `data:` + 空行分隔；注释帧（`: ping`）忽略。

    ⚠️ 只支持**单行 data**——agent 侧用 `json.dumps` 保证不换行（见 agent/src/main.py::_sse）。
    真要支持多行 data 得按行拼接，而拼接规则一旦写错就会产出半截 JSON；
    在"两端都是我们自己的服务"的前提下，把约束写在一处、用测试钉住，比支持一个用不到的形态划算。
    """
    event = ""
    buffer: list[str] = []
    for line in resp.iter_lines():
        if line == "":
            if buffer:
                yield _decode_sse_data(event or "message", "".join(buffer))
                event, buffer = "", []
            continue
        if line.startswith(":"):  # 保活注释帧
            continue
        if line.startswith("event:"):
            event = line[len("event:") :].strip()
        elif line.startswith("data:"):
            buffer.append(line[len("data:") :].lstrip())
    if buffer:
        yield _decode_sse_data(event or "message", "".join(buffer))


def stream_agent_chat(
    user_id: str, message: str, conversation_id: str, attachments: list[dict] | None = None
) -> Iterator[tuple[str, object]]:
    """调 agent 服务的**流式**接口，逐条产出 `(kind, payload)`。

    产出与 agent 侧 SSE 帧同构（见 `agent/src/main.py::chat_stream`）：
      ("status", dict) / ("delta", str) / ("tool", dict) / ("replace", str) / ("done", dict)

    与 `call_agent_chat` 的三条硬约束**完全一致**（改动前先读它的 docstring）：
      · user_id 由本后端注入；thread_id 派生 `f"{user_id}:{conversation_id}"`（归属校验）；
      · **单次尝试**不重试（对话非幂等，agent 会经工具写库）；
      · 失败抛 `AgentServiceError`，**绝不**在本地编造内容。

    ⚠️ `done` 里的 `reply` 是 agent 的**终稿**；`delta` 是**预览**（校验在 agent 侧流之后），
    调用方必须按「预览可覆盖、终稿才算数」处理（见 api/chat.py 的流式端点）。
    """
    thread_id = f"{user_id}:{conversation_id}"
    url = f"{settings.agent_service_base_url.rstrip('/')}/v1/chat/stream"
    payload = {"user_id": user_id, "message": message, "thread_id": thread_id}
    # 附件透传口径同 call_agent_chat（有值才带，空则 payload 形状不变）
    if attachments:
        payload["attachments"] = attachments
    # 读超时用既有 agent 超时（单轮 LLM 上限）；连接/写入用更短的固定值
    # ——连不上或写不出去没必要等 180 秒。
    timeout = httpx.Timeout(
        connect=10.0, read=settings.agent_service_timeout_s, write=30.0, pool=10.0
    )
    started = time.monotonic()
    saw_done = False
    try:
        with httpx.Client(timeout=timeout) as client:
            with client.stream("POST", url, json=payload, headers=_headers()) as resp:
                if resp.status_code >= 400:
                    detail = resp.read().decode("utf-8", "replace")[:200]
                    raise AgentServiceError(
                        f"Agent 服务返回 {resp.status_code}：{detail}", "http_error", 502
                    )
                for event, data in _iter_sse(resp):
                    if event == "error":
                        raise AgentServiceError(
                            str(data.get("message") or "Agent 服务流式调用失败"), "http_error", 502
                        )
                    if event in ("status", "tool"):
                        yield event, data
                    elif event == "delta":
                        yield "delta", str(data.get("text") or "")
                    elif event == "replace":
                        yield "replace", str(data.get("text") or "")
                    elif event == "done":
                        saw_done = True
                        yield "done", data
        # ⚠️ 流**提前结束**（agent 进程被杀/中间层截断）时不能当作"正常收尾"：
        # 上层会拿着空终稿去落库，结果是**一条空的 assistant 回复**（用户看到空气泡，
        # 且历史里永远留着这条脏数据）。宁可按"不可用"如实报错（CHAT_001/002），
        # 也绝不让空回复入库（同 §「失败不伪造」口径）。
        if not saw_done:
            raise AgentServiceError("Agent 服务流提前结束（未返回终稿）", "unavailable", 502)
    except httpx.TimeoutException as exc:
        raise AgentServiceError(
            f"Agent 服务响应超时（{settings.agent_service_timeout_s:.0f}s）", "unavailable", 504
        ) from exc
    except httpx.HTTPError as exc:
        raise AgentServiceError(f"Agent 服务连接失败：{exc}", "unavailable", 502) from exc
    logger.info(
        "agent 流式调用完成 user=%s thread=%s latency_ms=%d",
        user_id,
        thread_id,
        int((time.monotonic() - started) * 1000),
    )


def health(timeout: float = 5.0) -> dict:
    """探活（供运维 / 门禁 / `/healthz` 调用；失败不抛，返回状态字典）。

    2026-10-16（台账 §7.12）：`/healthz` 会调本函数把 agent 侧的**降级计数**
    （`body["degradation"]`，W1 记忆镜像 / W2 检索合并）暴露出来；`timeout` 可调，
    探活端点取更短的值以免一次依赖抖动拖慢健康检查（如 `/healthz` 传 2.0）。

    返回 `{"reachable","status","body"}`：`body` 为 agent `/health` 的 JSON（200 时），
    否则为状态文本或异常摘要（**不抛**——探活失败本身不该让调用方 500）。
    """
    url = f"{settings.agent_service_base_url.rstrip('/')}/health"
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url)
        return {"reachable": resp.status_code == 200, "status": resp.status_code,
                "body": resp.json() if resp.status_code == 200 else resp.text[:200]}
    except httpx.HTTPError as exc:
        return {"reachable": False, "status": None, "body": str(exc)}
