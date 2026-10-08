"""LLM 接入 —— 去 Coze 后的**模型配置单一真源**（2026-10-08 起支持双供应商）。

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
  · base_url 三种形态（**按 key 类型配对，不可混用**）：
      - **套餐专属 key（`sk-sp-`，Token Plan / Coding Plan）**：专属端点
        `https://token-plan.<region>.maas.aliyuncs.com/compatible-mode/v1`
        （官方 error-code.md：「…**必须配合各自的专属 Base URL 使用**，不可与通用 API Key/Base URL
          混用（混用会返回本鉴权错误）」；实测：sk-sp- key 打公共端点 → 401 invalid_api_key）
      - 空间级 key（`sk-ws-`）：**工作空间专属 Host**
        `https://[workspace-id].<region>.maas.aliyuncs.com/compatible-mode/v1`（官方模型库示例即此形态）
      - 通用 key（`sk-`）：**公共端点** `https://dashscope.aliyuncs.com/compatible-mode/v1`
        （官方文档原文：「SDK 调用配置的 base_url：https://dashscope.aliyuncs.com/compatible-mode/v1」）
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
from functools import lru_cache

logger = logging.getLogger("yishu.agent.llm")

# 默认模型（用户 2026-09-23 指定；官方模型库实证存在于百炼模型广场）
DEFAULT_MODEL = "qwen3.8-flash"
DEFAULT_REGION = "cn-beijing"
# 兼容模式公共端点（官方文档原文：「SDK 调用配置的 base_url：https://dashscope.aliyuncs.com/compatible-mode/v1」）
PUBLIC_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
# 套餐专属端点（Token Plan / Coding Plan）。官方 error-code.md 原文：
#   「套餐专属 API Key（Coding Plan / Token Plan 团队版）…以 `sk-sp-` 开头的专属 API Key，
#     **必须配合各自的专属 Base URL 使用**，不可与通用 API Key/Base URL 混用（混用会返回本鉴权错误）」
#   Token Plan 的 OpenAI 兼容端点：https://token-plan.<region>.maas.aliyuncs.com/compatible-mode/v1
# 实测印证：用 sk-sp- key 打公共端点 → 401 invalid_api_key（正是"混用"这条）。
TOKEN_PLAN_KEY_PREFIX = "sk-sp-"  # noqa: S105 —— 这是 key **前缀**（公开约定），不是密钥本身
TOKEN_PLAN_BASE_URL = "https://token-plan.{region}.maas.aliyuncs.com/compatible-mode/v1"  # noqa: S105 —— URL 模板，非凭据


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
    # W适配（2026-10-08）：False = 通用 OpenAI 兼容供应商（DeepSeek 等）——
    # 不发 X-DashScope-WorkSpace header、不发 enable_thinking extra_body
    #（那是百炼专有参数，发给别的供应商轻则被忽略、重则 400）。
    is_dashscope: bool = True

    def __hash__(self) -> int:
        # ⚠️ 必须显式定义：`build_chat_model` 挂了 @lru_cache，缓存键含 settings
        # ⇒ lru_cache 会对它做 hash。普通（非 frozen）dataclass 生成 __eq__ 后
        # __hash__ 被置 None ⇒ 一旦有人传 settings 实例就 TypeError: unhashable。
        # 生产一直用默认 settings=None 调用所以从未踩到——首个按 settings 调用的
        # 单元测试把它暴露了。dict 字段（extra_headers）转为排序元组参与哈希。
        return hash((
            self.api_key, self.base_url, self.model, self.enable_thinking,
            self.temperature, self.max_tokens, self.timeout, self.is_dashscope,
            tuple(sorted(self.extra_headers.items())),
        ))

    def describe(self) -> str:
        """可安全打印的摘要（**不含 api_key**）。"""
        return (
            f"model={self.model} base_url={self.base_url} "
            f"thinking={'on' if self.enable_thinking else 'off'} "
            f"temperature={self.temperature} max_tokens={self.max_tokens} "
            f"workspace_header={'yes' if self.extra_headers else 'no'} "
            f"dashscope={self.is_dashscope}"
        )


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_llm_settings() -> LlmSettings:
    """从环境变量装载模型配置；缺 key 直接抛错（不静默 mock）。

    ## 双供应商口径（2026-10-08，落实《外部API账号管理方案》"换模型=改配置不动代码"）

    **百炼（DashScope）路径**（默认，行为不变）：
      DASHSCOPE_API_KEY       百炼 API Key（sk-sp- 套餐 / sk-ws- 空间 / sk- 通用）
      DASHSCOPE_WORKSPACE_ID  业务空间 ID（用 sk-ws- key 时必填 → 决定 base_url 与 header）
      DASHSCOPE_REGION        默认 cn-beijing
      DASHSCOPE_BASE_URL      可选，整体覆盖 base_url（自建网关/私有化时用）

    **通用 OpenAI 兼容路径**（DeepSeek / 任意兼容网关；同名项优先级更高）：
      AGENT_LLM_API_KEY       供应商 key（设置后覆盖 DASHSCOPE_API_KEY）
      AGENT_LLM_BASE_URL      供应商 OpenAI 兼容端点（如 https://api.deepseek.com/v1；
                              设置后覆盖 DASHSCOPE_BASE_URL，且**跳过一切百炼专有行为**——
                              workspace header 与 enable_thinking extra_body 都不发）
      AGENT_LLM_MODEL         模型 id（两路径共用；DeepSeek 用 deepseek-chat）

    其余（两路径共用）：
      AGENT_ENABLE_THINKING   默认 true（仅百炼路径生效）
      AGENT_LLM_TEMPERATURE   默认 0.7
      AGENT_LLM_MAX_TOKENS    默认 2048
      AGENT_LLM_TIMEOUT       默认 120（秒）
    """
    api_key = (os.getenv("AGENT_LLM_API_KEY") or os.getenv("DASHSCOPE_API_KEY") or "").strip()
    if not api_key:
        raise LlmConfigError(
            "未配置模型 key —— 设 AGENT_LLM_API_KEY（任意 OpenAI 兼容供应商）"
            "或 DASHSCOPE_API_KEY（百炼）；见 agent/.env.example"
        )

    workspace_id = (os.getenv("DASHSCOPE_WORKSPACE_ID") or "").strip()
    region = (os.getenv("DASHSCOPE_REGION") or DEFAULT_REGION).strip()

    base_url = (os.getenv("AGENT_LLM_BASE_URL") or os.getenv("DASHSCOPE_BASE_URL") or "").strip()
    explicit_base_url = bool(base_url)
    if not base_url:
        if api_key.startswith(TOKEN_PLAN_KEY_PREFIX):
            # 套餐专属 key（sk-sp-）：**必须**配专属端点（官方：混用返回鉴权错误）
            base_url = TOKEN_PLAN_BASE_URL.format(region=region)
        elif workspace_id:
            # 空间级 key（sk-ws-）：官方要求走工作空间专属 Host 并带 X-DashScope-WorkSpace
            base_url = f"https://{workspace_id}.{region}.maas.aliyuncs.com/compatible-mode/v1"
        else:
            # 通用 key：公共端点
            base_url = PUBLIC_BASE_URL
    # W适配（2026-10-08）：非 aliyuncs 端点 ⇒ 通用 OpenAI 兼容供应商。
    # ⚠️ 这个判定必须先于 header/extra_body——workspace_id 是进程级环境变量，
    # 残留值不该泄漏给别的供应商（给 DeepSeek 发 X-DashScope-WorkSpace 毫无意义）。
    is_dashscope = "aliyuncs.com" in base_url
    # 配对校验：官方明确"不可混用"，混用会以鉴权错误收场。显式配置时只告警（让运维自己拍板），
    # 自动推导时上面已保证配对，故此处仅覆盖"显式覆盖成了不匹配端点"这一种情况。
    # （仅百炼路径做此校验；打到 DeepSeek 的 sk- key 无"配对"概念。）
    is_token_plan_url = "token-plan." in base_url
    if is_dashscope and explicit_base_url and api_key.startswith(TOKEN_PLAN_KEY_PREFIX) != is_token_plan_url:
        logger.warning(
            "百炼端点与 key 类型可能不匹配：key 前缀=%s，base_url=%s —— "
            "官方要求套餐专属 key（sk-sp-）与 Token Plan 专属端点配对，混用会返回鉴权错误",
            api_key[:6],
            base_url,
        )

    headers: dict[str, str] = {}
    if workspace_id and not is_dashscope:
        # 污染可见性：workspace 只属于百炼，别的供应商拿到它说明环境变量混了
        logger.warning(
            "忽略 DASHSCOPE_WORKSPACE_ID=%s：目标端点 %s 不是百炼（通用 OpenAI 兼容路径），"
            "workspace header 不适用。若非有意混用，请清 DASHSCOPE_* 环境变量",
            workspace_id[:8],
            base_url,
        )
    elif workspace_id:
        # ⚠️ 2026-10-05 修**真实 400**：`sk-sp-`（Token Plan）key **不绑定业务空间**，
        # 带上 X-DashScope-WorkSpace 必然被拒：
        #   400 The X-DashScope-WorkSpace header value must match the workspace bound to the API key.
        # 触发路径不是"有人手误"这么简单——`load_dotenv(override=False)` 让**进程环境变量优先于
        # .env**，于是陈旧的系统级 `sk-sp-` key（进程）会与 .env 里的 `DASHSCOPE_WORKSPACE_ID`
        # （文件）**拼成一份从未有人写过的 Frankenstein 配置**：token-plan 端点 + workspace header。
        # 此前"公共 key 带上 header 亦无副作用"的假设只对 `sk-` 成立，对 `sk-sp-` 是错的。
        # 处置：丢弃 workspace_id（token-plan 端点 + header 是确定的坏组合）并**显式告警**，
        # 让"配置被陈旧环境变量污染"这件事在日志里可见，而不是等 400 猜原因。
        if api_key.startswith(TOKEN_PLAN_KEY_PREFIX):
            logger.warning(
                "丢弃 DASHSCOPE_WORKSPACE_ID=%s：key 前缀=%s（Token Plan 套餐）不绑定业务空间，"
                "带 X-DashScope-WorkSpace 头会被百炼拒为 400。"
                "常见成因：进程里残留了另一套 DASHSCOPE_API_KEY，而 .env 里的 workspace 仍被读入"
                "（load_dotenv override=False ⇒ 进程环境变量优先，两边被拼成一份非法组合）。"
                "排查：清掉继承来的 DASHSCOPE_* 后重启（见 scripts/local_run.ps1）",
                workspace_id[:8],
                api_key[:6],
            )
        else:
            # 业务空间级 key（sk-ws-）必须显式带此 header；公共 key（sk-）带上亦无副作用
            headers["X-DashScope-WorkSpace"] = workspace_id

    settings = LlmSettings(
        api_key=api_key,
        base_url=base_url,
        model=(os.getenv("AGENT_LLM_MODEL") or DEFAULT_MODEL).strip(),
        # 2026-10-04 性能：**A/B 实测后保持默认开启**（推翻了我"关掉能降延迟"的假设）。
        # 同一 prompt、同一环境各跑 3 次：
        #   thinking=OFF → 25.3 / 17.5 / 14.5s，平均 19.1s
        #   thinking=ON  → 20.0 / 11.4 / 11.7s，平均 14.4s
        # 开着**反而更快**：推理模式帮模型判断"这条已经存过了"，从而少走工具循环
        # （工具循环才是延迟主因，单次 LLM 多花的那点时间不划算）。
        # 保持可关：设 AGENT_ENABLE_THINKING=0 可关闭做对照实验。
        enable_thinking=_env_bool("AGENT_ENABLE_THINKING", True),
        temperature=float(os.getenv("AGENT_LLM_TEMPERATURE") or 0.7),
        # 4096 -> 2048：实测回复均为百字内短文本，上限过大只会在失控时拖长等待
        max_tokens=int(os.getenv("AGENT_LLM_MAX_TOKENS") or 2048),
        # 600 -> 120 秒：单次调用超过 2 分钟基本等于挂了，快速失败好过长时间挂起
        timeout=float(os.getenv("AGENT_LLM_TIMEOUT") or 120),
        extra_headers=headers,
        is_dashscope=is_dashscope,
    )
    return settings


def reset_llm_cache() -> None:
    """清空模型客户端缓存（改了 LLM 相关环境变量后调用）。"""
    build_chat_model.cache_clear()
    logger.info("LLM 客户端缓存已清空")


@lru_cache(maxsize=2)
def build_chat_model(settings: LlmSettings | None = None, streaming: bool = True):
    """构建 langchain `ChatOpenAI`（**带缓存**；百炼或任意 OpenAI 兼容端点）。

    2026-10-04 性能：原实现每次调用都新建 `ChatOpenAI` ⇒ 每次都新建 httpx 连接池
    ⇒ TLS 握手与连接建立**完全无法复用**（agent 侧每请求调一次，实测明显开销）。
    现在按 (settings, streaming) 缓存复用。调用方一律走默认 `settings=None`
    （配置读一次即可），采样参数变更后调 `reset_llm_cache()`。

    注意：`extra_body` 用百炼官方的 `enable_thinking` 写法（上游误写成
    `{"thinking": {"type": ...}}`，实测恒 disabled）；**仅百炼路径携带**，
    通用供应商（DeepSeek 等）不发该非标准字段。返回对象只在 langchain-openai
    已安装时可用；未安装时抛 ImportError 并给出安装提示（不静默降级）。
    """
    settings = settings or load_llm_settings()
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:  # pragma: no cover - 环境缺依赖时的显式提示
        raise ImportError(
            "缺少 langchain-openai —— 请先安装 agent 服务依赖（见 agent/pyproject.toml）"
        ) from exc

    logger.info("模型接入：%s", settings.describe())
    # enable_thinking 是百炼专有参数（extra_body），只对 DashScope 发；
    # 发给 DeepSeek 等供应商轻则被忽略、重则 400（非标准字段不被容忍）。
    return ChatOpenAI(
        model=settings.model,
        api_key=settings.api_key,
        base_url=settings.base_url,
        temperature=settings.temperature,
        max_tokens=settings.max_tokens,
        timeout=settings.timeout,
        streaming=streaming,
        # 官方示例写法：推理开关放在 extra_body.enable_thinking
        extra_body=(
            {"enable_thinking": settings.enable_thinking} if settings.is_dashscope else None
        ),
        default_headers=settings.extra_headers or None,
    )
