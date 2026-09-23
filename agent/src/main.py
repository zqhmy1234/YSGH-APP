"""忆光 Agent 服务入口（去 Coze 版 · 2026-09-23 AG4b）。

**与上游 main.py 的关系**：上游把整个 Coze 平台外壳搬进来了——`/run`、`/stream_run`、
`/async_run`、`/node_run/{id}`、`/graph_parameter`、`/cancel/{run_id}`、`/v1/chat/completions`，
外加 `AsyncTaskRuntime`（Postgres 任务运行时）、`AgentStreamRunner`/`WorkflowStreamRunner`、
`OpenAIChatHandler`、`graph_helper`、`log.parser`/`node_log`、`cozeloop` 上报。
那些是 **Coze 平台自身的任务/流式/观测外壳**，与本系统无关，故**不重实现**；
本文件只暴露我们真正需要的接口（记忆域对话 + 健康检查 + 记忆浏览 web 面）。

**安全（上游最大的缺口，已在此补）**：
  · 上游**无任何鉴权**，数据归属只认可伪造的 `X-User-Id`（见 agent/UPSTREAM.md 五-1）；
  · 本服务**由我们后端反代调用**，`user_id` 只接受**后端注入**的请求体字段，
    并要求共享密钥头 `X-Agent-Token`（环境变量 `AGENT_SERVICE_TOKEN`；未配置时启动即告警，
    生产必须配置——本服务**不得直接暴露公网**）。
"""
from __future__ import annotations

# ruff: noqa: E402 —— 本文件**刻意**先 `load_dotenv(agent/.env)` 再导入依赖模块：
# `yishu.logging_setup` 在导入期读 `AGENT_LOG_LEVEL`、服务常量读 `AGENT_SERVICE_TOKEN`，
# 若先导入后加载 .env，这些值会取到默认值（静默走错）。故此豁免是有理由的、非疏漏。
import json
import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# ⚠️ 必须在读取任何环境常量**之前**加载 agent/.env（绝对路径，不依赖启动 CWD）
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

from yishu.context import new_context, request_context
from yishu.errors import classify_error
from yishu.logging_setup import setup_logging

from agents.agent import run_agent
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger("yishu.agent.service")

SERVICE_TOKEN = os.getenv("AGENT_SERVICE_TOKEN", "").strip()


class ChatRequest(BaseModel):
    """对话请求。

    `user_id` 由**我们后端**反代时注入（可信），客户端不得直连本服务。
    """

    user_id: str = Field(..., min_length=1, max_length=64)
    message: str = Field(..., min_length=1, max_length=8000)
    thread_id: str | None = Field(None, max_length=128, description="缺省=user_id（上游语义：按用户隔离上下文）")


class ChatResponse(BaseModel):
    reply: str
    user_id: str
    thread_id: str
    latency_ms: int


class HealthResponse(BaseModel):
    status: str
    model: str
    db_configured: bool
    auth_enabled: bool


def _normalize_reply(result: Any) -> str:
    """把 `run_agent_with_validation` 的返回规整成一段可回显文本。

    实测（2026-09-23 冒烟）：上游返回的是 **LangGraph 状态**（`{"messages": [...]}`，
    元素是 langchain 的 `HumanMessage`/`AIMessage` 等对象）——直接 `json.dumps` 会抛
    `Object of type HumanMessage is not JSON serializable`（服务 500）。
    故：① 优先取末条 AI 消息的 `content`；② 兜底 `json.dumps(default=str)` **永不因序列化失败而 500**。
    待 AG5 把返回收敛为契约字段后，这里可退化为直接取值。
    """
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        for key in ("reply", "content", "text", "message", "output"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value
        messages = result.get("messages")
        if isinstance(messages, (list, tuple)):
            for msg in reversed(messages):
                content: Any = getattr(msg, "content", None)
                if content is None and isinstance(msg, dict):
                    content = msg.get("content")
                if isinstance(content, str) and content.strip():
                    return content
                if isinstance(content, list):  # 多模态内容块列表
                    parts = [b.get("text", "") for b in content if isinstance(b, dict)]
                    joined = "".join(p for p in parts if p)
                    if joined.strip():
                        return joined
        return json.dumps(result, ensure_ascii=False, default=str)
    return str(result)


def _require_token(provided: str | None) -> None:
    """共享密钥校验（未配置时放行但已在启动日志告警——本地开发便利，生产必须配）。"""
    if SERVICE_TOKEN and provided != SERVICE_TOKEN:
        raise HTTPException(status_code=401, detail="X-Agent-Token 缺失或不匹配")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    model = os.getenv("AGENT_LLM_MODEL", "qwen3.8-flash")
    db_configured = bool(os.getenv("DATABASE_URL") or os.getenv("PGDATABASE_URL"))
    logger.info("忆光 Agent 服务启动：model=%s db_configured=%s", model, db_configured)
    if not SERVICE_TOKEN:
        logger.warning(
            "未配置 AGENT_SERVICE_TOKEN —— 本服务将不校验 X-Agent-Token。"
            "仅限本地开发；生产必须配置，且本服务不得直接暴露公网（user_id 必须来自后端注入）"
        )
    if not db_configured:
        raise RuntimeError("未配置 DATABASE_URL —— 记忆域数据落我们自己的 Postgres，必须配置")
    yield
    logger.info("忆光 Agent 服务停止")


app = FastAPI(title="忆光 Agent 服务", version="0.1.0", lifespan=lifespan)

# 记忆浏览 web 面（上游自带的演示/浏览用 API + 模板页；数据层已换成本地 Postgres）
try:
    from web.api_routes import router as web_router

    app.include_router(web_router)
except Exception as exc:  # noqa: BLE001 —— 该面非核心链路，缺失不应拖垮服务启动
    logger.warning("记忆浏览 web 面未挂载：%s", exc)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        model=os.getenv("AGENT_LLM_MODEL", "qwen3.8-flash"),
        db_configured=bool(os.getenv("DATABASE_URL") or os.getenv("PGDATABASE_URL")),
        auth_enabled=bool(SERVICE_TOKEN),
    )


@app.post("/v1/chat", response_model=ChatResponse)
def chat(req: ChatRequest, x_agent_token: str | None = Header(default=None)) -> ChatResponse:
    """一次对话（非流式）。

    多轮上下文：`thread_id` 缺省用 `user_id`，与上游 `run_agent` 的
    `config={"configurable": {"thread_id": user_id}}` 一致——**上游 HTTP 面把 thread_id 设成
    run_id 导致每次对话都是新的**（见 UPSTREAM 五-2），本接口刻意不沿用那个错误。
    """
    _require_token(x_agent_token)
    started = time.monotonic()
    thread_id = req.thread_id or req.user_id
    # 可信注入：user_id 只来自请求体（由后端反代填入），并写入请求上下文供工具读取
    ctx = new_context(method="chat", user_id=req.user_id, run_id=thread_id)
    request_context.set(ctx)
    try:
        result = run_agent(req.message, user_id=req.user_id, ctx=ctx)
    except Exception as exc:  # noqa: BLE001 —— 统一经分类器映射状态码
        classified = classify_error(exc)
        logger.exception("对话失败 kind=%s retryable=%s", classified.kind, classified.retryable)
        return JSONResponse(  # type: ignore[return-value]
            status_code=classified.http_status,
            content={
                "error": {"kind": classified.kind, "retryable": classified.retryable, "message": str(exc)},
                "user_id": req.user_id,
            },
        )
    latency_ms = int((time.monotonic() - started) * 1000)
    logger.info("对话完成 user=%s thread=%s latency_ms=%d", req.user_id, thread_id, latency_ms)
    return ChatResponse(
        reply=_normalize_reply(result),
        user_id=req.user_id,
        thread_id=thread_id,
        latency_ms=latency_ms,
    )


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
    classified = classify_error(exc)
    logger.exception("未处理异常 kind=%s", classified.kind)
    return JSONResponse(
        status_code=classified.http_status,
        content={"error": {"kind": classified.kind, "retryable": classified.retryable, "message": str(exc)}},
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("AGENT_HTTP_PORT", "8300"))
    uvicorn.run("main:app", host="127.0.0.1", port=port, workers=1)
