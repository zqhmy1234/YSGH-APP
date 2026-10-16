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
import queue
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

# ⚠️ 必须在读取任何环境常量**之前**加载 agent/.env（绝对路径，不依赖启动 CWD）
from dotenv import load_dotenv
from langchain_core.messages import AIMessage

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=False)

from agents.agent import run_agent, run_agent_stream
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from storage.memory.memory_saver import close_memory_pool, current_checkpointer_kind
from yishu import degradation
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
    backend_channel_token,
    is_production,
    require_token,
    token_fingerprint,
    validate_thread_id,
    validate_user_id,
)

logger = logging.getLogger("yishu.agent.service")

SERVICE_TOKEN = os.getenv("AGENT_SERVICE_TOKEN", "").strip()

_TRUTHY = {"1", "true", "yes", "on"}

# SSE 保活间隔（秒）：超过它没有事件就发一条注释帧（`: ping`）。
# 为什么需要：百炼 thinking 开启时首个**正文** token 可能十几秒后才出现，
# 期间链路完全静默——中间层或客户端可能按空闲超时断开，用户看到"AI 突然没了"。
_STREAM_HEARTBEAT_S = 10.0


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
    # 结构化附加（2026-10-09）：cards/chips。由 `utils.output_validator.build_reply_meta`
    # 从 `save_memory` 的**工具结果**派生（不解析回复文本、不猜提示词协议）。
    # 缺省空 dict ⇒ 旧调用方行为不变（非记忆类对话本就没有结构化形态）。
    meta: dict[str, Any] = Field(default_factory=dict, description="cards/chips（对话页卡片与追问建议）")


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
    # ── 2026-10-14 新增（台账 §7.12「静默降级显性化」）─────────────────────────
    # 与 checkpointer 同因：W1 记忆镜像 / W2 检索合并失败时**照常 200、对话照常
    # 往返**，只是记忆不进 App 时间轴、App 里的图文 AI 也搜不到——探活接口不报
    # 出来，就没有任何人能发现。`degradation.total` 非 0 即代表有路径正在降级。
    inbound_token_fp: str = ""           # 本服务校验用的 AGENT_SERVICE_TOKEN 指纹
    backend_token_configured: bool = False
    backend_token_fp: str = ""           # agent→backend 令牌指纹（与 backend 启动日志对账）
    backend_base_url: str = ""
    degradation: dict[str, Any] = Field(default_factory=dict)


def _normalize_reply(result: Any) -> str:
    """把 `run_agent_with_validation` 的返回规整成一段可回显文本。

    实测（2026-09-23 冒烟）：上游返回的是 **LangGraph 状态**（`{"messages": [...]}`，
    元素是 langchain 的 `HumanMessage`/`AIMessage` 等对象）——直接 `json.dumps` 会抛
    `Object of type HumanMessage is not JSON serializable`（服务 500）。
    故：① 只取**末条 AIMessage** 的 content；② LangGraph 状态扫不到 AI 正文 ⇒ 返回**空串**；
    ③ 真·未知形态才走 `json.dumps(default=str)` 兜底（永不因序列化失败而 500）。

    ⚠️ 2026-10-11 静态审查修复（B-1/B-2，同根两病）：
    · B-2：倒序扫描**必须过滤消息类型**——重试轮把「[校验反馈] 上一次回复存在以下问题：…」
      的 HumanMessage 追加进消息列表，原实现无类型判断，重试轮正文为空时这条**内部校验
      提示词会被当成回复**推给客户端并落库（内部协议泄漏上屏）。只认 AIMessage
      （dict 元素保守保留——09-23 以来的防御形态），HumanMessage/ToolMessage 一律排除。
    · B-1：LangGraph 状态（含 `messages` 键）但无任何 AI 正文时，原兜底
      `json.dumps(result)` 会把 `{"messages":[],"_diag":…}` **整包诊断 JSON 当成回复**
      落库并展示——「没终稿绝不落空回复」被换了个形态绕过。改为返回空串，交给两端的
      空终稿守卫（agent 非流式 / backend 流式与非流式）如实报 CHAT_001，绝不伪造内容。
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
                # B-2：HumanMessage（校验反馈）/ToolMessage（工具结果）绝不作为回复来源
                if not isinstance(msg, (AIMessage, dict)):
                    continue
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
            if "messages" in result:
                # B-1：LangGraph 状态但无 AI 正文 ⇒ 空串（空终稿守卫接住，报 CHAT_001）
                return ""
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
        # ── 2026-10-14（台账 §7.12）：服务间通道指纹 ─────────────────────────
        # 把入站（AGENT_SERVICE_TOKEN）与出站（AGENT_BACKEND_TOKEN 回落同类键）
        # 的**指纹**打进启动日志：与 backend 启动日志的 fp 比对，即可判定
        # "两侧是不是同一把密钥"，不必去猜。两侧不一致时 W1/W2 会**静默降级**，
        # 而两边日志都只显示"已配置"——那正是要消灭的形态。
        # 指纹不可逆（sha256 前 8 位）且只进日志，不进任何对外响应。
        # ⚠️ 字段名不得含 `token_value` 类禁止词（`yishu.observability._reject_sensitive`
        # 会**抛异常**而非跳过）；`*_token_fp` 经核对是安全的。
        inbound_token_fp=token_fingerprint(),
        backend_token_configured=bool(backend_channel_token()),
        backend_token_fp=token_fingerprint(backend_channel_token()),
        backend_base_url=(
            os.getenv("AGENT_BACKEND_BASE_URL") or "http://127.0.0.1:8010"
        ).strip().rstrip("/"),
        **_agent_summary_fields(agent_runtime_summary()),
    )
    # backend 可达性自检（W2/W1 依赖，2026-10-06）：
    # 检索合并（W2）与记忆镜像（W1）都走 backend；它不可达时两者静默降级，
    # 表现为「对话正常但搜不到 App 记忆 / 记忆不进时间轴」——正是最危险的
    # 「看起来正常实际是假的」。启动时探一次 /healthz（2s 超时），不可达只
    # WARNING 不阻断（降级路径已保证对话可用），让运维在启动日志里就能看到。
    _backend_base = (os.getenv("AGENT_BACKEND_BASE_URL") or "http://127.0.0.1:8010").strip().rstrip("/")
    try:
        import httpx

        with httpx.Client(timeout=2.0) as _probe:
            _resp = _probe.get(f"{_backend_base}/healthz")
        backend_ok = _resp.status_code == 200
    except Exception as _exc:  # noqa: BLE001 —— 探活失败本身不该影响启动
        backend_ok = False
        logger.info("backend 探活异常: %s", type(_exc).__name__)
    log_event(
        "startup_backend_reachable",
        backend_base=_backend_base,
        ok=backend_ok,
        hint=None if backend_ok else "检索合并/记忆镜像将降级；检查 backend 是否已启动",
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
    # 退出前关掉 checkpointer 连接池：psycopg_pool 有后台 worker 线程
    # （num_workers，默认 3）会一直维护池大小，不 close 就等于泄漏。
    # 单连接时代不需要这一步，是池化后新增的义务（见 memory_saver.close_memory_pool）。
    close_memory_pool()
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
    outbound_token = backend_channel_token()
    return HealthResponse(
        status="ok",
        model=os.getenv("AGENT_LLM_MODEL", "qwen3.8-flash"),
        db_configured=bool(os.getenv("DATABASE_URL") or os.getenv("PGDATABASE_URL")),
        auth_enabled=bool(SERVICE_TOKEN),
        checkpointer=current_checkpointer_kind(),
        # 2026-10-14（台账 §7.12）：把"增强路径是否在降级"变成可探活的事实。
        inbound_token_fp=token_fingerprint(),
        backend_token_configured=bool(outbound_token),
        backend_token_fp=token_fingerprint(outbound_token),
        backend_base_url=(
            os.getenv("AGENT_BACKEND_BASE_URL") or "http://127.0.0.1:8010"
        ).strip().rstrip("/"),
        degradation=degradation.snapshot(),
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
        meta = result.get("_meta") if isinstance(result, dict) else None
        reply = _normalize_reply(result)
        if not reply.strip():
            # B-1 配套（2026-10-11）：agent 整轮跑完却没有 AI 正文（模型整轮未吐字/
            # empty_messages）。返回 200+空 reply 会被 backend 落库成空气泡（"没终稿
            # 绝不落空回复"在这里同样成立）；诊断信息已由 log_event 全量落盘，可复盘。
            # 与上游不可用同形态返回（backend 侧映射为 CHAT_001）。
            logger.error("agent 返回空回复 user=%s thread=%s", user_id, thread_id)
            return JSONResponse(  # type: ignore[return-value]
                status_code=502,
                content={
                    "error": {
                        "kind": "upstream",
                        "retryable": True,
                        "message": "服务暂时不可用，请稍后重试",
                    },
                    "user_id": user_id,
                },
            )
        return ChatResponse(
            reply=reply,
            user_id=user_id,
            thread_id=thread_id,
            latency_ms=latency_ms,
            meta=meta if isinstance(meta, dict) else {},
        )
    finally:
        reset_request_id(rid_token)


def _sse(event: str, data: dict) -> str:
    """把一条事件编成 SSE 帧（`event:` + 单行 JSON `data:`）。

    ⚠️ `data` 必须落成**单行**（`json.dumps` 默认不换行；`ensure_ascii=False` 保留中文）——
    SSE 的多行 data 得拆成多条 `data:` 行，任何手写拼接都可能把换行漏出去，
    客户端按行解析就拿到半截 JSON（表现为"偶尔解析失败"这种极难复现的故障）。
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/v1/chat/stream")
def chat_stream(req: ChatRequest, x_agent_token: str | None = Header(default=None)) -> StreamingResponse:
    """一次对话（**SSE 流式**）：token 增量 + 工具状态 + 终稿。

    与 `/v1/chat` 的关系：**同一套鉴权/限流/线程归属/校验口径**，差别只在传输形态。
    两个端点都保留——非流式供后端批量/脚本与降级路径用，流式供客户端逐字渲染。

    帧格式（`event:` + 单行 JSON `data:`）：
      · `status`  {"stage": "accepted"}                  —— 立刻回一帧，证明链路通了
      · `delta`   {"text": "…"}                          —— 第 1 轮 token 增量（**预览**）
      · `tool`    {"phase": "started"|"done", "name": …} —— 工具状态
      · `replace` {"text": "<整段>"}                     —— 校验重试后预览作废，**整体替换**
      · `done`    {"reply": "<终稿>", "latency_ms": …}   —— **终稿**，客户端以此收尾
      · `error`   {"kind","retryable","message"}         —— 失败（**不含异常原文**）

    ⚠️ 响应头一旦发出就改不了状态码：入参/鉴权/限流失败仍在**生成器之前**抛出，
    客户端拿到真实 HTTP 状态码；流开始之后的任何失败只能落在 `error` 帧里。
    ⚠️ 预览（delta）**不是终稿**：输出校验发生在流之后（见 output_validator 流式段注释），
    故调用方必须按「预览带"生成中"水印、终稿整体替换」展示，绝不把预览当最终答案存档。
    """
    _require_token(x_agent_token)
    started = time.monotonic()
    # 入口校验留在生成器**之外**：这些失败要能被客户端当真实 HTTP 状态码看到
    user_id = validate_user_id(req.user_id)
    thread_id = validate_thread_id(req.thread_id, user_id)
    allowed, remaining = _CHAT_RATE_LIMITER.check(user_id)
    if not allowed:
        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    ctx = new_context(method="chat_stream", user_id=user_id, run_id=thread_id)

    def _frames():
        # ⚠️ 为什么用「工作线程 + 队列」而不是直接在生成器里迭代 run_agent_stream：
        # Starlette 会把**同步生成器的每一次 `next()` 派发到线程池**（anyio to_thread，
        # 每次调用都取一份新的 contextvars 快照）。于是：
        #   ① `request_context.set(ctx)`（工具层读 user_id 的唯一来源）在第二次 `next()`
        #      时就没了 —— 记忆工具会拿不到 user_id（fail-closed 会拒、或更糟地串档）；
        #   ② `bind_request_id` 的 token 跨 `next()` 重置会抛
        #      `ValueError: was created in a different Context`（本批次实测踩到）。
        # 把整轮 agent 运行放进**一个线程**：ContextVar 全程有效，request_id 也能正确回收。
        # 队列侧只做字符串格式化，无 ContextVar 依赖。
        events: queue.Queue = queue.Queue()
        stop = threading.Event()

        def _worker() -> None:
            request_context.set(ctx)
            rid_token = bind_request_id(new_request_id())
            try:
                log_event(
                    "chat_stream_start",
                    user_id=user_id,
                    thread_id=thread_id,
                    msg_len=len(req.message or ""),
                    thread_explicit=req.thread_id is not None,
                )
                for item in run_agent_stream(req.message, user_id=user_id, ctx=ctx):
                    if stop.is_set():
                        return
                    events.put(item)
            except Exception as exc:  # noqa: BLE001 —— 交给主线程转 error 帧
                events.put(("__error__", exc))
            finally:
                # 同一线程内绑定/重置 ⇒ token 合法；跨线程重置才会炸（见上方注释）
                reset_request_id(rid_token)
                events.put(("__end__", None))

        threading.Thread(target=_worker, name="chat-stream", daemon=True).start()
        yield _sse("status", {"stage": "accepted"})
        try:
            while True:
                try:
                    kind, payload = events.get(timeout=_STREAM_HEARTBEAT_S)
                except queue.Empty:
                    # 保活注释帧：长思考（百炼 thinking）期间若长时间无字节，
                    # 中间层/客户端可能按超时断开，用户看到"AI 突然没了"
                    yield ": ping\n\n"
                    continue
                if kind == "__end__":
                    return
                if kind == "__error__":
                    classified = classify_error(payload)
                    log_event(
                        "chat_stream_error",
                        level=logging.ERROR,
                        user_id=user_id,
                        thread_id=thread_id,
                        kind=classified.kind,
                        retryable=classified.retryable,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        exc_type=type(payload).__name__,
                    )
                    logger.exception(
                        "流式对话失败 kind=%s retryable=%s", classified.kind, classified.retryable
                    )
                    yield _sse(
                        "error",
                        {
                            "kind": classified.kind,
                            "retryable": classified.retryable,
                            "message": _client_message(classified),
                        },
                    )
                    return
                if kind == "delta":
                    yield _sse("delta", {"text": payload})
                elif kind == "tool":
                    yield _sse("tool", payload)
                elif kind == "replace":
                    yield _sse("replace", {"text": payload})
                elif kind == "final":
                    reply = _normalize_reply(payload)
                    latency_ms = int((time.monotonic() - started) * 1000)
                    diag = payload.get("_diag") if isinstance(payload, dict) else None
                    diag = diag or {}
                    log_event(
                        "chat_stream_done",
                        user_id=user_id,
                        thread_id=thread_id,
                        latency_ms=latency_ms,
                        reply_len=len(reply),
                        rounds=diag.get("rounds"),
                        llm_calls=diag.get("llm_calls"),
                        tool_calls=diag.get("tool_calls"),
                        validator_status=diag.get("status"),
                        agent_ms=diag.get("agent_ms"),
                    )
                    meta = payload.get("_meta") if isinstance(payload, dict) else None
                    yield _sse(
                        "done",
                        {
                            "reply": reply,
                            "user_id": user_id,
                            "thread_id": thread_id,
                            "latency_ms": latency_ms,
                            "meta": meta if isinstance(meta, dict) else {},
                        },
                    )
        finally:
            # 客户端断开 ⇒ 生成器被 close：置位让 worker 在下一个事件处停下
            # （已开始的 LLM 调用无法打断，这是刻意的：记忆写入不该被半途掐掉）
            stop.set()

    return StreamingResponse(
        _frames(),
        media_type="text/event-stream",
        headers={
            # 中间层（nginx/隧道）默认会缓冲整段响应，那等于把流式退化成非流式
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


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
