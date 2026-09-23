"""
忆光 - 个人AI记忆助手
帮助用户记录、整理、搜索和管理个人生活碎片
"""
import json
from pathlib import Path
from typing import Annotated

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


def _windowed_messages(old, new):
    """滑动窗口: 只保留最近 MAX_MESSAGES 条消息"""
    return add_messages(old, new)[-MAX_MESSAGES:]  # type: ignore


class AgentState(MessagesState):
    messages: Annotated[list[AnyMessage], _windowed_messages]


@wrap_tool_call
def handle_tool_errors(request, handler):
    """处理工具执行错误，返回友好的错误消息"""
    try:
        return handler(request)
    except Exception as e:
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
      · `config/agent_llm_config.json` 仍作 **system prompt（sp）的唯一真源**（V3 标签体系 SOP
        长文）；其 `model` / 采样参数改由环境变量承载（见 `.env.example`）。
      · 配置路径不再依赖 `COZE_WORKSPACE_PATH`，改为相对本文件解析（仓库内自洽）。
    """
    config_path = Path(__file__).resolve().parents[2] / LLM_CONFIG
    with open(config_path, encoding='utf-8') as f:
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

    return agent


def run_agent(query: str, user_id: str = "default", ctx=None, max_rounds: int = 3):
    """
    带输出校验的 Agent 调用入口

    :param query: 用户输入
    :param user_id: 用户标识（用于数据隔离和对话隔离）
    :param ctx: 请求上下文
    :param max_rounds: 最大校验重试轮数（默认3轮）
    :return: Agent 执行结果
    """
    from platform.context import new_context, request_context

    agent = build_agent(ctx)

    # 将 user_id 注入到 request_context，供工具函数获取
    if ctx:
        ctx.user_id = user_id
    else:
        ctx = new_context(method="agent_run")
        ctx.user_id = user_id
    request_context.set(ctx)

    # 使用 user_id 作为 thread_id，实现对话上下文隔离
    config = {"configurable": {"thread_id": user_id}}
    return run_agent_with_validation(agent, query, config, max_rounds)
