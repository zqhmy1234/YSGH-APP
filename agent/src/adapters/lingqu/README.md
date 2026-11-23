# Lingqu（灵渠）

模型提供商小模块。名称取自灵渠：连接不同水系的枢纽，对应「多个 provider 之间的路由与统一出口」。

## 结构

- `catalog.py` / `config/lingqu-catalog.json`：五家 profile；默认 `deepseek/deepseek-v4.1-flash`。
- `credentials.py`：`COMPANY_LLM_API_KEY` 统一 Key，`LINGQU_<PROVIDER>_API_KEY` 单家覆盖。
- `engine.py`：唯一 import `litellm` 的地方；`FakeEngine` 供测试。
- `router.py`：按 Agent purpose 选 profile；只在 429/5xx/timeout 时 fallback。
- `gateway.py`：实现 `LLMPort`，业务代码只依赖它。
- `admin.py` / `cli.py`：`list` / `use` / `probe` / `doctor` / `catalog-check` / `routes`。

## 快速命令

```bash
PYTHONPATH=src python -m agentv3.lingqu.cli catalog-check
PYTHONPATH=src python -m agentv3.lingqu.cli list
PYTHONPATH=src python -m agentv3.lingqu.cli doctor
LINGQU_LIVE=1 PYTHONPATH=src python -m agentv3.lingqu.cli probe deepseek/deepseek-v4.1-flash
```

## 环境变量

```text
COMPANY_LLM_API_KEY=...            # 公司统一 key
COMPANY_LLM_BASE_URL=...           # 可选，公司 OpenAI 兼容网关
LINGQU_DEEPSEEK_API_KEY=...        # 可选，单家覆盖
LINGQU_QWEN_API_KEY=...
LINGQU_KIMI_API_KEY=...
LINGQU_MINIMAX_API_KEY=...
LINGQU_GLM_API_KEY=...
LINGQU_DEFAULT_PROFILE=deepseek/deepseek-v4.1-flash
LINGQU_PROFILE_MUSE=deepseek/deepseek-v4-pro
LINGQU_FALLBACK_CHAIN=qwen/qwen4-max,glm/glm-5.3
LINGQU_FALLBACK_ENABLED=0          # 关闭故障 fallback
LINGQU_SELECTION_PATH=...          # 可选，持久化 active profile 的 JSON
LINGQU_CATALOG_PATH=...            # 可选，覆盖目录文件
LINGQU_TIMEOUT_S=30
```

密钥只从环境/Infisical 注入；`doctor` 只显示 `credential_source` 与 sha256 前 8 位。
