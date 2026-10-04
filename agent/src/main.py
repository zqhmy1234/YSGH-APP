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

from agents.agent import run_agent
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from storage.memory.memory_saver import current_checkpointer_kind
from yishu.context import new_context, request_context
from yishu.errors import classify_error
from yishu.logging_setup import setup_logging
from yishu.observability import (
    bind_request_id,
    log_event,
    new_request_id,
    reset_request_id,
)
from yishu.security import (
    RateLimiter,
    assert_production_token_config,
    is_production,
    require_token,
    validate_thread_id,
    validate_user_id,
)

logger = logging.getLogger("yishu.agent.service")

SERVICE_TOKEN = os.getenv("AGENT_SERVICE_TOKEN", "").strip()

_TRUTHY = {"1", "true", "yes", "on"}


def _env_true(key: str, default: bool = False) -> bool:
    """读布尔环境变量（口径统一，避免各处自己写 truthy 判断）。"""
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in _TRUTHY


def _is_production() -> bool:
    """是否生产环境。

    ⚠️ 2026-10-05：本函数原先是**独立实现**（认 `APP_ENV_PRODUCTION` 或 `APP_ENV`），
    而真正决定 fail-closed 的 `yishu.security.is_production()` 只认 `APP_ENV`。
    两处口径不一致的后果不是"多认一种环境"，而是**告警文案与实际行为相反**：
    只设 `APP_ENV_PRODUCTION=true` 时，本函数为真 ⇒ 启动告警里写
    「生产会拒绝启动」，而 `assert_production_token_config()` 实际**不会**拒绝。
    运维据此以为已 fail-closed，实际仍裸奔——比没有告警更危险。
    故此处不再自建判定，统一取 security 的那一份（单一真相源）。
    """
    return is_production()


def _agent_summary_fields(summary: dict) -> dict:
    """把 agent 运行时摘要摊平成可安全入日志的标量字段。

    只取计数/开关类（工具数、是否单例等），**不取任何 prompt 或工具入参**
    —— 隐私纪律见 yishu/observability 模块 docstring。
    """
    return {
        f"agent_{k}": v for k, v in (summary or {}).items() if isinstance(v, (int, float, bool))
    }


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
    # 2026-10-04 新增：checkpointer 的**真实**类型。
    # 为什么必须暴露：降级到 MemorySaver 时服务**照常 200、对话照常往返**，
    # 只是一轮轮都是新会话（用户以为有记忆、实际每次失忆）——这是最典型的
    # 「看起来正常实际是假的」。探活接口不报出来，就没有任何人能发现。
    checkpointer: str = "未初始化"


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


def _client_message(classified) -> str:
    """把错误类别映射成**可对外**的中文文案（不含异常原文）。

    为什么必须有这一层：`/v1/chat` 由我们后端反代调用，但**不保证**只有它能调用；
    而异常原文（DB DSN、绝对路径、SQL）对调用方没有任何价值，只有风险。
    非生产环境返回 `debug_hint` 只是类别名，同样不含内部信息。
    """
    messages = {
        "config": "服务配置不完整，请联系管理员",
        "auth": "请求未通过身份校验",
        "rate_limit": "请求过于频繁，请稍后再试",
        "timeout": "处理超时，请稍后重试",
        "upstream": "上游服务暂时不可用，请稍后重试",
        "bad_input": "请求内容不合法",
        "internal": "服务内部错误，请稍后重试",
    }
    return messages.get(classified.kind, "服务内部错误，请稍后重试")


def _require_token(provided: str | None) -> None:
    """共享密钥校验（已收敛到 `yishu.security.require_token`，两处口径必须一致）。

    2026-10-04：原先本函数与 `web/api_routes.py` 各写一套，而**后者那套完全没有校验**
    ⇒ `/api/memories?user_id=<任意 uuid>` 可读任何人记忆。现统一到 `yishu.security`，
    规则只有一份，加固不会漏掉旁路。
    """
    require_token(provided)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    model = os.getenv("AGENT_LLM_MODEL", "qwen3.8-flash")
    db_configured = bool(os.getenv("DATABASE_URL") or os.getenv("PGDATABASE_URL"))
    logger.info("忆光 Agent 服务启动：model=%s db_configured=%s", model, db_configured)
    if not db_configured:
        raise RuntimeError("未配置 DATABASE_URL —— 记忆域数据落我们自己的 Postgres，必须配置")
    # 启动自检（2026-10-04）：把「配错了但能跑」的配置全部显式打出来。
    # 目的是让**延迟与安全口径可核对**——优化前后对比要看的就是这些值。
    from agents.agent import agent_runtime_summary

    # ⚠️ 这里的默认值必须与 `llm_provider.load_llm_settings` 实际口径**一致**，
    # 否则启动日志会说 thinking=False 而运行时是 True（2026-10-04 曾出现此不一致：
    # A/B 实测证明 thinking 默认应为 True，见 llm_provider 的 A/B 数据）。
    from llm_provider import load_llm_settings

    llm_settings = load_llm_settings()
    log_event(
        "startup_config",
        model=model,
        checkpointer=current_checkpointer_kind(),
        auth_enabled=bool(SERVICE_TOKEN),
        enable_thinking=llm_settings.enable_thinking,
        max_tokens=llm_settings.max_tokens,
        llm_timeout_s=int(llm_settings.timeout),
        validator_max_rounds=int(os.getenv("AGENT_MAX_ROUNDS", "2")),
        validator_budget_s=float(os.getenv("AGENT_VALIDATOR_BUDGET_S", "20")),
        rate_limit_rpm=int(os.getenv("AGENT_RATE_LIMIT_RPM", "30")),
        web_panel=WEB_PANEL_MOUNTED,
        **_agent_summary_fields(agent_runtime_summary()),
    )
    # 生产鉴权 fail-closed：启动即拒绝，不给「忘了配也能起」留机会。
    # 规则已收敛到 yishu.security（与 web/api_routes.py 共用同一份）。
    assert_production_token_config()
    if not SERVICE_TOKEN:
        logger.warning(
            "未配置 AGENT_SERVICE_TOKEN —— 本服务不校验 X-Agent-Token。"
            "仅限本地开发；生产（APP_ENV=production）会拒绝启动，"
            "且本服务不得直接暴露公网"
        )
    yield
    logger.info("忆光 Agent 服务停止")


app = FastAPI(title="忆光 Agent 服务", version="0.1.0", lifespan=lifespan)

# 对话限流（进程内按 user_id 滑动窗口；阈值 AGENT_RATE_LIMIT_RPM，默认 30/分钟）
_CHAT_RATE_LIMITER = RateLimiter.from_env()

# 记忆浏览 web 面（上游自带的演示/浏览用 API + 模板页；数据层已换成本地 Postgres）
WEB_PANEL_MOUNTED = False
try:
    from web.api_routes import router as web_router

    app.include_router(web_router)
    WEB_PANEL_MOUNTED = True
except Exception as exc:  # noqa: BLE001 —— 该面非核心链路，缺失不应拖垮服务启动
    logger.warning("记忆浏览 web 面未挂载：%s", exc)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    #只读单例既有状态：**探活不得触发建连/建表**（否则 /health 变成会改状态的接口）
    return HealthResponse(
        status="ok",
        model=os.getenv("AGENT_LLM_MODEL", "qwen3.8-flash"),
        db_configured=bool(os.getenv("DATABASE_URL") or os.getenv("PGDATABASE_URL")),
        auth_enabled=bool(SERVICE_TOKEN),
        checkpointer=current_checkpointer_kind(),
    )


@app.post("/v1/chat", response_model=ChatResponse)
def chat(req: ChatRequest, x_agent_token: str | None = Header(default=None)) -> ChatResponse:
    """一次对话（非流式）。

    多轮上下文：`thread_id` 缺省用 `user_id`，与上游 `run_agent` 的
    `config={"configurable": {"thread_id": user_id}}` 一致——**上游 HTTP 面把 thread_id 设成
    run_id 导致每次对话都是新的**（见 UPSTREAM 五-2），本接口刻意不沿用那个错误。

    埋点（2026-10-04）：入口/出口都带 `rid`，与 LLM/工具/校验层日志串联。
    **不记录消息正文**，只记长度（隐私纪律见 yishu/observability 模块 docstring）。
    """
    _require_token(x_agent_token)
    started = time.monotonic()
    rid_token = bind_request_id(new_request_id())
    try:
        # 2026-10-04 安全加固：格式 + 归属校验，缺一即拒（fail-closed）。
        # `user_id` 虽只由后端反代注入，仍校验格式——它会进 SQL `.eq("user_id", ...)`
        # 与日志，脏值会同时污染两处。
        # `thread_id` 校验归属：checkpointer 按 thread_id 存 checkpoint，不校验归属时
        # A 用户指定 B 的 thread_id 就能读到 B 的对话历史（agent 实测第 2 轮会准确
        # 复述第 1 轮内容——正是「记忆正常」的表现，也正是泄漏面）。
        user_id = validate_user_id(req.user_id)
        thread_id = validate_thread_id(req.thread_id, user_id)
        # 按 user_id 限流：单用户狂刷会直接打爆 LLM 配额与账单。
        # 超限返回 429（如实告知「稍后再试」）而不是静默丢弃。
        allowed, remaining = _CHAT_RATE_LIMITER.check(user_id)
        if not allowed:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
        log_event("chat_rate_ok", user_id=user_id, remaining=remaining)
        # 可信注入：user_id 只来自请求体（由后端反代填入），并写入请求上下文供工具读取
        ctx = new_context(method="chat", user_id=user_id, run_id=thread_id)
        request_context.set(ctx)
        log_event(
            "chat_start",
            user_id=user_id,
            thread_id=thread_id,
            msg_len=len(req.message or ""),
            thread_explicit=req.thread_id is not None,
        )
        try:
            result = run_agent(req.message, user_id=user_id, ctx=ctx)
        except Exception as exc:  # noqa: BLE001 —— 统一经分类器映射状态码
            classified = classify_error(exc)
            latency_ms = int((time.monotonic() - started) * 1000)
            log_event(
                "chat_error",
                level=logging.ERROR,
                user_id=user_id,
                thread_id=thread_id,
                kind=classified.kind,
                retryable=classified.retryable,
                latency_ms=latency_ms,
                exc_type=type(exc).__name__,
            )
            logger.exception("对话失败 kind=%s retryable=%s", classified.kind, classified.retryable)
            return JSONResponse(  # type: ignore[return-value]
                status_code=classified.http_status,
                content={
                    "error": {
                        "kind": classified.kind,
                        "retryable": classified.retryable,
                        # ⚠️ 2026-10-05 安全：原先回显 `str(exc)`。异常原文里可能带
                        # 数据库 DSN（含密码）、服务器文件绝对路径、SQL 片段——而这个
                        # 响应体会**原样进入我们后端的日志**，再间接流向客户端。
                        # 改为「固定文案 + 错误类别」；排查所需细节已由上面的
                        # `logger.exception` 完整落盘，日志侧不丢信息。
                        "message": _client_message(classified),
                    },
                    "user_id": user_id,
                },
            )
        latency_ms = int((time.monotonic() - started) * 1000)
        # 诊断字段：把 agent 内部统计带出来，便于回答"时间花在哪"
        diag = result.get("_diag") if isinstance(result, dict) else None
        diag = diag or {}
        log_event(
            "chat_done",
            user_id=user_id,
            thread_id=thread_id,
            latency_ms=latency_ms,
            reply_len=len(_normalize_reply(result)),
            rounds=diag.get("rounds"),
            llm_calls=diag.get("llm_calls"),
            tool_calls=diag.get("tool_calls"),
            tool_dist=diag.get("tool_dist"),
            validator_status=diag.get("status"),
            agent_ms=diag.get("agent_ms"),
            usage=diag.get("usage"),
        )
        return ChatResponse(
            reply=_normalize_reply(result),
            user_id=user_id,
            thread_id=thread_id,
            latency_ms=latency_ms,
        )
    finally:
        reset_request_id(rid_token)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:  # pragma: no cover
    classified = classify_error(exc)
    logger.exception("未处理异常 kind=%s", classified.kind)
    return JSONResponse(
        status_code=classified.http_status,
        content={
            "error": {
                "kind": classified.kind,
                "retryable": classified.retryable,
                # 同 /v1/chat：异常原文只进日志，不进响应体（可能含 DSN/绝对路径）
                "message": _client_message(classified),
            }
        },
    )


if __name__ == "__main__":
    import uvicorn

    port = int(os.getenv("AGENT_HTTP_PORT", "8300"))
    uvicorn.run("main:app", host="127.0.0.1", port=port, workers=1)
