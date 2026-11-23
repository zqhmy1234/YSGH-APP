# 现有 Agent 代码复用清单（P0 产物）

> 盘点对象：`develop` 分支上的旧 `agent/src`（7839 行）+ 后端相关服务。
> 新落点：`agent/src/agentv3/`。结论先给：**复用行为，不复用文件**；旧代码不删，两条路径零 import。

## 1. 直接复用（行为/常量/规则）

| 现有代码 | 行数 | 复用到哪里 | 复用了什么 | 变了什么 |
|---|---|---|---|---|
| `agent/src/agents/agent.py` | 452 | `agents/muse.py` + `orchestrator.py` | 单轮限步、工具调用错误不炸整轮、消息窗口思路 | 剥掉 13 个写工具；只留只读工具；工具表由组合根注入 |
| `agent/src/utils/output_validator.py` | 903 | `agents/muse.py`（`validate_reply`）+ `obs.py` | 非法回复降级、空/超长拦截、成本与耗时记账 | 903 行单文件拆成两个小函数；不再用 LLM 自评当正确性判据 |
| `agent/src/tools/memory_tools.py` | 1574 | `memory/ledger.py` + `agents/sculptor.py` | 幂等键、重复查找、写入前后校验、审计日志 | 模型写权收回；写入改批次 + 19 issue code + 整批回滚 |
| `agent/src/main.py` | 645 | `api/routes.py` | token 校验、`/health`、错误返回形状 | 路由只做转发；业务进 orchestrator；新增 4 个 v3 端点 |
| `agent/src/yishu/degradation.py` | 50 | `obs.py`（`Degradation`） | 按原因计数、快照 | 与 `TurnResult.degraded_ports` 直连 |
| `agent/src/yishu/observability.py` | 311 | `obs.py`（`Tracer`/`redact`） | span 记录、敏感字段脱敏 | 用 stdlib 计时 + 递归脱敏，去掉框架依赖 |
| `agent/src/yishu/security.py` | 249 | `memory/policy.py` | 敏感词/危机词思路 | 危机改常量 + 固定话术，不依赖敏感词表 |
| `backend/app/services/rag.py` | 323 | `adapters/backend.py`（`BackendSearch`） | 通道划分（keyword/dense/sparse/time） | 客户端化；返回契约对象；失败抛 PortError |
| `backend/app/services/llm_ops/*` | 1409 | `agents/provenance.py` 规则 + `memory/policy.py` | 输出护栏/审核思路 | 规则引擎化（四条规则），不调用模型也能判 |

## 2. 只借鉴不改写

| 现有代码 | 借鉴点 | 为什么不再复制一遍 |
|---|---|---|
| `backend/app/api/chat.py`（596 行） | SSE 三层帧、断连不丢回复 | 本轮不接流式；`sse-streaming-integration` skill 已沉淀范式 |
| `backend/app/services/external/agent.py`（214） | 对话不重试（非幂等） | 该判断已写进 `BackendLLM` 的失败语义（超时即 PortTimeout，不自动重试） |
| `client/` 的 UTS 工具链 | 真机链路口径 | 与 Agent 域解耦，改客户端需另读 HBuilderX skill |

## 3. 明确不复用

- `save_memory` 作为模型可直接调用的写工具（写权模型化，与新架构冲突）。
- `output_validator` 的「LLM 自评内容是正确」路径（自证循环）。
- `sensitive_words` 当危机通道（词表不能承担 T3 处置）。
- 旧 `AgentState` / LangGraph 图结构（新架构是显式 plan + 组合根，不引入图运行时）。

## 4. 复用账目

- 旧代码总行数：`agent/src` 7839；新包：约 3.2k（含测试外的实现与提示词）。
- 被复用的旧行为：**6 类**（限步、降级、脱敏、幂等、通道划分、规则护栏）。
- 未复用的旧文件：**0 删除**（旧路径保持可运行，切换只在部署层）。
