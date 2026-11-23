# Lingqu 模型网关（L0 · 2026-11-23）

## 为什么叫 Lingqu

「灵渠」是秦代连通湘江与漓江的运河：一次修渠，多条水系按需分流。模型网关做的是同一件事——上层只说「我要一次对话」，下层按 profile 把请求送到 DeepSeek / Qwen / Kimi / MiniMax / GLM，任何一家都不需要进入业务代码。名字取「灵渠」的 `Lingqu`，不与旧 `llm_provider` 混淆。

成熟实现参考并复用其**路由/适配思路**，没有直接引入框架：

- LiteLLM（MIT）：统一 completion 参数与 provider 前缀，本模块唯一 SDK 边界。
  - https://www.litellm.ai/about
  - https://docs.litellm.ai/docs/
- OpenRouter 的「模型目录 + 显式选择 + 失败回退」产品形态（仅参考，未接服务）。

没有直接采用 LangChain `ChatModel` 作为网关的原因：它会把握手、重试、回调、工具 schema 全部塞进业务对象；V3 的 `LLMPort` 只需要 `complete(request) -> response`，LiteLLM 的薄适配更可控。

## 目录（唯一配置真源）

`agent/config/lingqu-catalog.json`

- 5 家 provider、13 个 profile，默认 `deepseek/deepseek-v4-flash`。
- 每条 profile 必须带 `source`（官方文档 URL）与 `verified_at`；`default_profile` 必须是 DeepSeek，否则加载失败。
- 模型名、上下文窗口、最大输出、能力（chat/tools/json/reasoning/search/vision）都在目录里，不在代码里。

| Provider | Profiles | 官方文档 |
|---|---|---|
| DeepSeek | `deepseek-v4-flash`、`deepseek-v4-pro` | https://api-docs.deepseek.com/zh-cn |
| Qwen | `qwen4-max`、`qwen3.8-plus` | https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions |
| Kimi | `kimi-k3.1-pro`、`kimi-k2.6` | https://platform.kimi.ai/docs/guide/kimi-k2-quickstart |
| MiniMax | `MiniMax-M3.1-Flash`、`M3`、`M2.5` | https://platform.minimaxi.com/docs/guides/text-generation |
| GLM | `glm-5.3`、`5.2`、`5.1`、`4.7-flash` | https://docs.bigmodel.cn/cn/guide/models/text/glm-5.3 |

## 统一 Key 与单家覆盖

```bash
# 公司统一 Key：默认给所有 profile 用
COMPANY_LLM_API_KEY=...
# 可选：公司聚合网关地址（留空则用各官方 base）
COMPANY_LLM_BASE_URL=
# 可选：单家覆盖（公司 key 不覆盖某家时使用）
LINGQU_DEEPSEEK_API_KEY=
LINGQU_QWEN_API_KEY=
LINGQU_KIMI_API_KEY=
LINGQU_MINIMAX_API_KEY=
LINGQU_GLM_API_KEY=
```

Qwen 的 workspace 专属域名：

```bash
LINGQU_QWEN_WORKSPACE_ID=...   # 或 COMPANY_LLM_WORKSPACE_ID / QWEN_WORKSPACE_ID
```

缺 workspace 时**回退**到 `https://dashscope.aliyuncs.com/compatible-mode/v1`，不会拼出 `{workspace_id}` 的坏 URL。Kimi 也支持 `LINGQU_KIMI_BASE_URL` 覆盖 `.ai` / `.cn`。

凭据纪律：只在 `SecretResolver` 里读取；`Credential.__repr__` 与 `doctor()` 只显示 `source` + sha256 前 8 位 fingerprint；HTTP `list_models()` 永不返回 key。

## 路由与回退

- 按 purpose 选主 profile：`muse` / `librarian_rewrite` / `provenance` / `sculptor` / `digest`，默认都走 DeepSeek。
- 覆盖：`LINGQU_PROFILE_<PURPOSE>`（例如 `LINGQU_PROFILE_SCULPTOR=glm/glm-5.3`）。
- 失败回退：默认 `qwen/qwen4-max,glm/glm-5.3`，可用 `LINGQU_FALLBACK_CHAIN` 改，`LINGQU_FALLBACK_ENABLED=0` 关。
- **只有** 429 / 5xx / timeout / connection 可回退；400 / 401 / 403 / context overflow / content filter **不**回退。400 是调用方 bug，回退只会掩盖。
- 某家凭据缺失时跳过该候选，继续下一个；全部不可用才抛 `PortUnavailable`。
- `LINGQU_TIMEOUT_S` 默认 30 秒。

## HTTP / CLI

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/v3/models` | 13 个 profile、active、能力、脱敏 fingerprint |
| GET | `/v1/v3/models/active` | 当前 active profile（不是列表第一项） |
| PUT | `/v1/v3/models/active` | 切换，`{"profile_id":"qwen/qwen4-max"}`；未知 404 |
| POST | `/v1/v3/models/probe` | 发最小请求；无 key 返回 `unavailable`，有 key 才发网络 |
| GET | `/health` | `deps.llm` = ok / missing-key / degraded / unconfigured |

```bash
cd agent
PYTHONPATH=src .venv312/bin/python -m agentv3.lingqu.cli catalog-check
PYTHONPATH=src .venv312/bin/python -m agentv3.lingqu.cli list
PYTHONPATH=src .venv312/bin/python -m agentv3.lingqu.cli use qwen/qwen4-max
```

`active` 持久化到 `LINGQU_SELECTION_PATH`（JSON，只存 profile id，不存 key）。

## 测试与验证边界

- 17 个 Lingqu 单测：目录完整性/默认 DeepSeek/重复 id、统一 key/单家覆盖/ Qwen workspace、429 回退、400 不回退、缺 key 跳过、probe、admin 不泄 key。
- **真实厂商调用尚未验证**：本机没有 `COMPANY_LLM_API_KEY`。`FakeEngine` 通过只证明目录/凭据/路由/协议，不证明网络。
- 有 key 后跑：`LINGQU_LIVE=1 COMPANY_LLM_API_KEY=... bash scripts/dev/agent_v3_smoke.sh`。L0 smoke 会显式标注 `SKIP live model probe`，不会伪造 live pass。

## 参考

- LiteLLM provider 前缀：`deepseek/` `dashscope/` `moonshot/` `minimax/` `zhipu/`
- 测试期默认 LLM：**DeepSeek**（用户 2026-11-23 指定）
