# vendor/linger — 上游参考快照（只读，不参与运行）

本目录**不是运行依赖**：新架构的 Python 移植件在 `agent/src/agentv3/`。这里保留
上游钉版（`6663e21a4f07004def7591b1e07e4725c5c73e60`）的**参考材料**，用于对照与
升级前复核。上游许可为 Apache-2.0，归属声明见仓库根 `NOTICE`。

## 为什么目录里暂时只有规则摘要

本机拉取 `raw.githubusercontent.com` 在本轮施工中超时（`Recv failure`），因此
未把 TypeScript 原文完整复制进来，避免留下半截文件冒充 vendored 源码。需要完整
原文时执行：

```bash
cd /Users/silver/GHapp
python scripts/upstream_diff.py --online   # 带网络时按钉版 sha 拉取并打印差异
```

## 已对照的上游文件与本地落点

| 上游 | 本地移植件 | 状态 |
|---|---|---|
| `apps/node-services/src/memory/apply.ts` | `agent/src/agentv3/memory/ledger.py` | ported |
| `apps/node-services/src/memory/README.md` | `agent/src/agentv3/agents/librarian.py` | ported |
| `apps/node-services/src/memory-agent.ts` | `agent/src/agentv3/agents/sculptor.py` | adapted |
| `apps/node-services/src/llm-wiki/wiki-candidate-validation.ts` | `agent/src/agentv3/agents/sculptor.py` | ported |
| `apps/node-services/prompts/memory-agent/principles.md` | `agent/src/agentv3/prompts/sculptor.md` | adapted |

规则摘要见同目录 `RULES.md`；逐文件状态与不复用清单见 `agent/docs/upstream/linger.json`。
