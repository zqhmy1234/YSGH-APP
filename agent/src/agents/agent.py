"""
忆光 - 个人AI记忆助手
帮助用户记录、整理、搜索和管理个人生活碎片
"""
import json
import logging
import os
import time
from pathlib import Path
from typing import Annotated, Any

from langchain.agents import create_agent
from langchain.agents.middleware import wrap_tool_call
from langchain_core.messages import AnyMessage, ToolMessage
from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages

# 去 Coze（2026-09-23）：模型接入收归 src/llm_provider（百炼 OpenAI 兼容端点，配置只认 .env）；
# 同时移除两处已成死引用的上游导入——`ChatOpenAI`（原在此直接构造）与
# `coze_coding_utils...default_headers`（原给 LLM 传 Coze 上下文头，现已无调用点）。
from llm_provider import build_chat_model
from storage.memory.memory_saver import get_memory_saver

# 工具导入
from tools.memory_tools import (
    delete_memory,
    get_knowledge_summary,
    get_memory_stats,
    get_recent_memories,
    save_knowledge_collection,
    save_memory,
    search_knowledge_collections,
    search_memories,
    update_memory_summary,
)
from tools.url_fetch_tools import (
    delete_knowledge_collection,
    export_knowledge_collections,
    fetch_url_content,
    link_memory_to_collection,
)
from utils.output_validator import run_agent_with_validation

LLM_CONFIG = "config/agent_llm_config.json"

# 默认保留最近 20 轮对话 (40 条消息)
MAX_MESSAGES = 40

logger = logging.getLogger("yishu.agent.core")

# 单例 agent 缓存（2026-10-04 性能优化）：原先每次请求都重建模型客户端与
# checkpointer 连接，连接池无法复用。缓存键 = system prompt 文件的 mtime，
# 改prompt 会自动失效重建。
_AGENT_CACHE: dict | None = None
# 无工具 agent（仅供输出校验的重试轮使用，见 build_agent_no_tools）
_AGENT_NO_TOOLS: Any = None


def _windowed_messages(old, new):
    """滑动窗口: 只保留最近 MAX_MESSAGES 条消息"""
    return add_messages(old, new)[-MAX_MESSAGES:]  # type: ignore


class AgentState(MessagesState):
    messages: Annotated[list[AnyMessage], _windowed_messages]


@wrap_tool_call
def handle_tool_errors(request, handler):
    """处理工具执行错误，返回友好的错误消息。

    2026-10-04：此处原先**零日志**。工具失败被转成 ToolMessage 后，LLM 只看到
    「操作遇到问题」，不知道发生了什么，于是反复重试**同一个**工具——实测单轮
    `save_memory` 被调 16 次、耗时 32s、输入 token 涨到 13K、最后撞递归上限。
    记 tool 名 + 异常类型是定位这类循环的唯一线索。
    """
    try:
        return handler(request)
    except Exception as e:  # noqa: BLE001 —— 兜底转消息，故意的宽 catch
        tool_name = "unknown"
        try:
            tool_name = (request.tool_call or {}).get("name", "unknown")
        except Exception:  # noqa: BLE001,S110 —— 取名字失败不能连带影响主流程
            pass
        from yishu.observability import log_event

        log_event(
            "tool_error",
            level=logging.WARNING,
            tool=tool_name,
            exc_type=type(e).__name__,
            detail=str(e)[:150],
        )
        return ToolMessage(
            content=f"操作遇到问题：{str(e)}。请检查输入后重试。",
            tool_call_id=request.tool_call["id"]
        )


def build_agent(ctx=None):
    """构建忆光记忆助手 Agent（去 Coze 版：模型接入见 src/llm_provider.py）。

    与上游的差异（2026-09-23）：
      · 模型不再走 Coze 网关（`COZE_INTEGRATION_MODEL_BASE_URL` + workload identity key），
        改用百炼 OpenAI 兼容端点，配置只认 `.env`（`src/llm_provider.py:load_llm_settings`）。
      · **修正上游 bug**：上游读 `cfg['config'].get('thinking')`，而配置文件的键是
        `thinking_type` ⇒ `.get()` 恒取默认 `'disabled'`，**推理一直是关的**；真实参数名是
        `extra_body.enable_thinking`（官方示例写法，已在 llm_provider 中落地并实测返回
        `reasoning_content`）。
      · `config/agent_llm_config.json` 仍作**system prompt（sp）的唯一真源**（V3 标签体系 SOP
        长文）；其 `model` / 采样参数改由环境变量承载（见 `.env.example`）。
      · 配置路径不再依赖 `COZE_WORKSPACE_PATH`，改为相对本文件解析（仓库内自洽）。

    2026-10-04 性能：**单例复用**。原实现每请求都重读配置 JSON + 新建 `ChatOpenAI`
    （新连接池 ⇒ TLS 握手与连接建立无法复用）。现在按「system prompt 文件 mtime」
    作为缓存键——改了 prompt 会自动失效重建，改了采样参数则需显式
    `reset_agent_cache()`（因为 `load_llm_settings` 自己带 lru_cache）。
    """
    global _AGENT_CACHE  # noqa: PLW0603 —— 单例缓存的标准写法
    config_path = Path(__file__).resolve().parents[2] / LLM_CONFIG
    mtime = config_path.stat().st_mtime if config_path.exists() else 0.0

    cached = _AGENT_CACHE
    if cached is not None and cached.get("mtime") == mtime:
        return cached["agent"]

    started = time.perf_counter()
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)

    llm = build_chat_model()

    # 定义Agent可用的工具
    tools = [
        save_memory,
        search_memories,
        get_recent_memories,
        get_memory_stats,
        update_memory_summary,
        delete_memory,
        save_knowledge_collection,
        search_knowledge_collections,
        get_knowledge_summary,
        fetch_url_content,
        delete_knowledge_collection,
        export_knowledge_collections,
        link_memory_to_collection
    ]

    agent = create_agent(
        model=llm,
        system_prompt=cfg.get("sp"),
        tools=tools,
        middleware=[handle_tool_errors],
        checkpointer=get_memory_saver(),
        state_schema=AgentState,
    )

    _AGENT_CACHE = {"mtime": mtime, "agent": agent}
    from yishu.observability import log_event

    log_event(
        "agent_built",
        tool_count=len(tools),
        build_ms=int((time.perf_counter() - started) * 1000),
        cached=False,
    )
    return agent


def reset_agent_cache() -> None:
    """显式清空 agent 单例（改了 LLM 采样参数/env 后调用）。

    `build_chat_model` 自身带 lru_cache，只清 agent 不够——模型客户端也要重建。
    """
    global _AGENT_CACHE  # noqa: PLW0603 —— 单例缓存的标准写法
    _AGENT_CACHE = None
    try:
        from llm_provider import reset_llm_cache

        reset_llm_cache()
    except Exception as exc:  # noqa: BLE001 —— 缓存清理失败不该阻断服务
        logger.warning("清理 LLM 客户端缓存失败: %s", exc)


def build_agent_no_tools(ctx=None):
    """构建**无工具**的 agent（输出校验的重试轮用）。

    2026-10-04：重试轮的目的只是「让 LLM 按反馈重写回复文本」。
    若仍带工具，一次校验失败就会再触发整条工具链——实测第二轮重复调了
    16~28 次工具（写库副作用 + 数十秒）。无工具重试把成本从「一次完整
    agent 循环」降到「一次纯文本生成」，且**副作用归零**。
    """
    global _AGENT_NO_TOOLS  # noqa: PLW0603 —— 单例缓存的标准写法
    if _AGENT_NO_TOOLS is not None:
        return _AGENT_NO_TOOLS

    config_path = Path(__file__).resolve().parents[2] / LLM_CONFIG
    with open(config_path, encoding="utf-8") as f:
        cfg = json.load(f)
    _AGENT_NO_TOOLS = create_agent(
        model=build_chat_model(),
        system_prompt=cfg.get("sp"),
        tools=[],  # 关键：无工具
        middleware=[handle_tool_errors],
        checkpointer=get_memory_saver(),
        state_schema=AgentState,
    )
    logger.info("已构建无工具 agent（仅用于输出校验重试轮）")
    return _AGENT_NO_TOOLS


def agent_runtime_summary() -> dict:
    """运行时摘要（供启动自检日志；只含计数/开关，不含 prompt 与工具入参）。

    2026-10-04：`/health` 与启动日志要能看出「这个服务现在到底按什么在跑」——
    工具数、模型、是否走单例、checkpointer 类型，都是排延迟问题时第一时间要看的。
    """
    return {
        "tools": 13,
        "model": os.getenv("AGENT_LLM_MODEL", "qwen3.8-flash"),
        "prompt_path": LLM_CONFIG,
        "agent_cached": _AGENT_CACHE is not None,
    }


def run_agent(query: str, user_id: str = "", ctx=None, max_rounds: int | None = None):
    """
    带输出校验的 Agent 调用入口

    :param query: 用户输入
    :param user_id: 用户标识（用于数据隔离和对话隔离）
    :param ctx: 请求上下文
    :param max_rounds: 最大校验重试轮数；None = 用 `AGENT_MAX_ROUNDS`（默认 2）
    :return: Agent 执行结果（含 `_diag` 诊断字段）
    """
    from yishu.context import new_context, request_context

    # 2026-10-04 隔离 fail-closed：user_id 为空直接拒绝。
    # 原签名默认值是 "default"，等于「没注入用户就落到共享桶」——
    # 一旦触发，save_memory 写进公共身份、search_memories 能搜到别人的记忆。
    # 这里改为**拒绝执行**，让上层在入口就拿到明确错误，而不是静默串档。
    if not (user_id or "").strip():
        from yishu.observability import log_audit

        log_audit("agent_missing_user_id", method=(ctx.method if ctx else "unknown"))
        raise ValueError("user_id 缺失，拒绝执行（防止跨用户串档）")

    agent = build_agent(ctx)

    # 将 user_id 注入到 request_context，供工具函数获取
    if ctx:
        ctx.user_id = user_id
    else:
        ctx = new_context(method="agent_run")
        ctx.user_id = user_id
    request_context.set(ctx)

    # 对话上下文隔离：优先用调用方给的 thread_id（run_agent HTTP 入口传的是
    # 会话 id），缺省才退回 user_id。
    # 2026-10-04 修正：原先硬编码 `thread_id=user_id`，**完全忽略 ctx.run_id**，
    # 导致 HTTP 层算出的 thread_id 形同虚设（同一用户不同会话会并成一条）。
    thread_id = (getattr(ctx, "run_id", None) or user_id).strip()
    # recursion_limit：LangGraph 默认 25 步，在「LLM 反复调同一工具」时等于
    # 让用户干等 30~60 秒才失败。降到 10 让它**快速失败**并留下日志，
    # 而不是让用户盯着转圈。可用环境变量按场景调高。
    try:
        recursion_limit = int(os.getenv("AGENT_RECURSION_LIMIT", "10"))
    except (TypeError, ValueError):
        recursion_limit = 10
    config = {
        "configurable": {"thread_id": thread_id},
        "recursion_limit": recursion_limit,
    }
    return run_agent_with_validation(agent, query, config, max_rounds)
