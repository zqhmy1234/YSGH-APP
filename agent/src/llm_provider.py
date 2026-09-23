"""百炼（DashScope）LLM 接入 —— 去 Coze 后的**模型配置单一真源**。

为什么单独成文件（而不是继续写在 agents/agent.py 里）：
  上游把模型网关写死在 `agents/agent.py:74-90`（`COZE_INTEGRATION_MODEL_BASE_URL` +
  Coze workload identity key），并且 **推理开关的键名写错了**——代码读
  `cfg['config'].get('thinking')`，而 `config/agent_llm_config.json` 里写的键是
  `thinking_type` ⇒ `.get()` 恒取到默认值 `'disabled'`，**推理一直是关的**。
  这里把「模型 id / base_url / 鉴权 header / 推理开关」收成一处，供 agent 主链与后续
  子服务共用；配置来源只认环境变量（`.env`），不再有厂商专有平台调用。

官方依据（2026-09-23 取自百炼官方模型库 `models/groups/qwen3.8-flash.json` 与官方示例代码）：
  · 模型 ID `qwen3.8-flash`：上下文 1,000,000（maxInput 991,808 / maxOutput 131,072）；
    能力 TG + VU + Reasoning；features 含 function-calling / structured-outputs / cache；
    定价 输入 0.8、输出 2.7 元每百万 tokens。
  · base_url 形态：`https://[workspace-id].<region>.maas.aliyuncs.com/compatible-mode/v1`
  · 推理开关：`extra_body={"enable_thinking": True}`（**不是** `thinking.type`，也不是
    顶层 `thinking`；官方示例即此写法）。
  · 业务空间级 key（`sk-ws-` 前缀）：HTTP 调用须带 header
    `X-DashScope-WorkSpace: <workspace-id>`（官方文档原文：「通过 HTTP 调用时，请指定
    Header 中的 X-DashScope-WorkSpace」）。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger("yishu.agent.llm")

# 默认模型（用户 2026-09-23 指定；官方模型库实证存在于百炼模型广场）
DEFAULT_MODEL = "qwen3.8-flash"
DEFAULT_REGION = "cn-beijing"


class LlmConfigError(RuntimeError):
    """配置缺失/非法。**必须显式抛出**——本项目铁律：宁可启动即失败，不要静默降级成假数据。"""


@dataclass
class LlmSettings:
    api_key: str
    base_url: str
    model: str
    enable_thinking: bool
    temperature: float
    max_tokens: int
    timeout: float
    extra_headers: dict[str, str] = field(default_factory=dict)

    def describe(self) -> str:
        """可安全打印的摘要（**不含 api_key**）。"""
        return (
            f"model={self.model} base_url={self.base_url} "
            f"thinking={'on' if self.enable_thinking else 'off'} "
            f"temperature={self.temperature} max_tokens={self.max_tokens} "
            f"workspace_header={'yes' if self.extra_headers else 'no'}"
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_llm_settings() -> LlmSettings:
    """从环境变量装载模型配置；缺 key 直接抛错（不静默 mock）。

    变量：
      DASHSCOPE_API_KEY       必填，百炼 API Key（token plan）
      DASHSCOPE_WORKSPACE_ID  业务空间 ID（用 sk-ws- key 时必填 → 决定 base_url 与 header）
      DASHSCOPE_REGION        默认 cn-beijing
      DASHSCOPE_BASE_URL      可选，整体覆盖 base_url（自建网关/私有化时用）
      AGENT_LLM_MODEL         默认 qwen3.8-flash
      AGENT_ENABLE_THINKING   默认 true（qwen3.8-flash 支持推理；关掉可降延迟）
      AGENT_LLM_TEMPERATURE   默认 0.7
      AGENT_LLM_MAX_TOKENS    默认 4096
      AGENT_LLM_TIMEOUT       默认 600（秒）
    """
    api_key = (os.getenv("DASHSCOPE_API_KEY") or "").strip()
    if not api_key:
        raise LlmConfigError(
            "未配置 DASHSCOPE_API_KEY —— 百炼 API Key 必填（agent/.env，见 agent/.env.example）"
        )

    workspace_id = (os.getenv("DASHSCOPE_WORKSPACE_ID") or "").strip()
    region = (os.getenv("DASHSCOPE_REGION") or DEFAULT_REGION).strip()

    base_url = (os.getenv("DASHSCOPE_BASE_URL") or "").strip()
    if not base_url:
        if not workspace_id:
            raise LlmConfigError(
                "未配置 DASHSCOPE_WORKSPACE_ID，且 DASHSCOPE_BASE_URL 为空 —— "
                "两者至少给一个（业务空间级 key 必须带空间 ID，官方文档要求 HTTP 调用带 "
                "X-DashScope-WorkSpace header）"
            )
        base_url = f"https://{workspace_id}.{region}.maas.aliyuncs.com/compatible-mode/v1"

    headers: dict[str, str] = {}
    if workspace_id:
        # 业务空间级 key（sk-ws-）必须显式带此 header；公共 key 带上亦无副作用
        headers["X-DashScope-WorkSpace"] = workspace_id

    settings = LlmSettings(
        api_key=api_key,
        base_url=base_url,
        model=(os.getenv("AGENT_LLM_MODEL") or DEFAULT_MODEL).strip(),
        enable_thinking=_env_bool("AGENT_ENABLE_THINKING", True),
        temperature=float(os.getenv("AGENT_LLM_TEMPERATURE") or 0.7),
        max_tokens=int(os.getenv("AGENT_LLM_MAX_TOKENS") or 4096),
        timeout=float(os.getenv("AGENT_LLM_TIMEOUT") or 600),
        extra_headers=headers,
    )
    return settings


def build_chat_model(settings: LlmSettings | None = None, streaming: bool = True):
    """构建 langchain `ChatOpenAI`（百炼 OpenAI 兼容端点）。

    注意：`extra_body` 用官方的 `enable_thinking`（上游误写成 `{"thinking": {"type": ...}}`，
    实测恒 disabled）。返回对象只在 langchain-openai 已安装时可用；未安装时抛 ImportError
    并给出安装提示（不静默降级）。
    """
    settings = settings or load_llm_settings()
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:  # pragma: no cover - 环境缺依赖时的显式提示
        raise ImportError(
            "缺少 langchain-openai —— 请先安装 agent 服务依赖（见 agent/pyproject.toml）"
        ) from exc

    logger.info("百炼模型接入：%s", settings.describe())
    return ChatOpenAI(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
        timeout=settings.timeout,
        streaming=streaming,
        # 官方示例写法：推理开关放在 extra_body.enable_thinking
        extra_body={"enable_thinking": settings.enable_thinking},
        default_headers=settings.extra_headers or None,
    )
