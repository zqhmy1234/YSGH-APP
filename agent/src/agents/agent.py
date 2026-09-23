"""
忆光 - 个人AI记忆助手
帮助用户记录、整理、搜索和管理个人生活碎片
"""
import os
import json
from typing import Annotated

from langchain.agents import create_agent
from langchain.agents.middleware import wrap_tool_call
from langchain_openai import ChatOpenAI
from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage, ToolMessage

from coze_coding_utils.runtime_ctx.context import default_headers
from storage.memory.memory_saver import get_memory_saver
from utils.output_validator import run_agent_with_validation

# 工具导入
from tools.memory_tools import (
    save_memory,
    search_memories,
    get_recent_memories,
    get_memory_stats,
    update_memory_summary,
    delete_memory,
    save_knowledge_collection,
    search_knowledge_collections,
    get_knowledge_summary
)
from tools.url_fetch_tools import (
    fetch_url_content,
    delete_knowledge_collection,
    export_knowledge_collections,
    link_memory_to_collection
)

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
    """构建忆光记忆助手Agent"""
    workspace_path = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
    config_path = os.path.join(workspace_path, LLM_CONFIG)

    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)

    api_key = os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY")
    base_url = os.getenv("COZE_INTEGRATION_MODEL_BASE_URL")

    llm = ChatOpenAI(
        model=cfg['config'].get("model"),
        api_key=api_key,
        base_url=base_url,
        temperature=cfg['config'].get('temperature', 0.7),
        streaming=True,
        timeout=cfg['config'].get('timeout', 600),
        extra_body={
            "thinking": {
                "type": cfg['config'].get('thinking', 'disabled')
            }
        },
        default_headers=default_headers(ctx) if ctx else {}
    )

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
    from coze_coding_utils.log.write_log import request_context
    from coze_coding_utils.runtime_ctx.context import new_context

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
