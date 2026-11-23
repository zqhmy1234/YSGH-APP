# Linger 代码级复用登记（Apache-2.0 合规）

## 上游

- 仓库：`https://github.com/simplexai-labs/linger`
- 版本锚点：commit `6663e21a4f07004def7591b1e07e4725c5c73e60`
- 许可：Apache License 2.0（条款 1 允许作为自有应用后端使用；1.a 的书面授权仅针对把 Linger 源码作为托管服务转售，本项目不涉及）
- 结论：允许内嵌/改造复用；义务为保留版权与许可声明（本文件 + 文件头注释）。

## 复用分级

| 级别 | 含义 | 本项目落点 |
|---|---|---|
| L1 等价移植 | 语义逐条对齐，语言换成 Python | `memory/ledger.py`、`agents/librarian.py`、`agents/sculptor.py`、`memory/projection.py` |
| L2 行为移植 | 保留机制，参数按本项目预算调整 | `frame.py`（裁剪顺序）、`orchestrator.py`（限步/降级） |
| L3 只搬常量/公式 | RRF k=60、alternates≤6、19 issue codes | `librarian.py`、`sculptor.py` |
| L4 明确不复用 | TeamHub、FTS 内核、前端、本地模型栈、`apps/web` 品牌资产 | 不出现 |

## 逐文件对照

| 本项目文件 | Linger 来源 | 保留了什么 | 改了什么 |
|---|---|---|---|
| `memory/ledger.py` | `apps/node-services/src/memory/apply.ts` | 批量校验、快照、整批回滚、应用写 change log、valid/record 双时间 | TypeScript → Python；PG → 领域对象 + SQLite 适配器；新增 `CAME_FROM` |
| `agents/librarian.py` | `src/memory/README.md`（检索与证据协议） | RRF(k=60)、≤6 alternates 各投一票、时间只投票不过滤、摘要/原文两列、空结果先查原文、降级不静默 | 去掉 Node 端 SQL；端口化（SearchPort/EventPort）；`clarify` 只在无降级且歧义时给 |
| `agents/sculptor.py` | `src/memory-agent.ts` + `src/llm-wiki/wiki-candidate-validation.ts` | 限步 propose/validate/finish、19 个 issue code、整批通过才落盘、同文本归并 | 去掉 LLM 对话外壳，只保留确定性的 propose/validate/apply |
| `memory/projection.py` | `docs/wiki-and-memory.md`（视图边界） | digest 只读投影、署名段落、缺失即 `unavailable` | 段名改为本产品八段；预算 600 字符 |
| `frame.py` | `src/memory-agent.ts` 的上下文预算思路 | 固定裁剪顺序、稳定段/易变段分离 | 九段结构与 4200 字符预算为本项目定义 |
| `prompts/sculptor.md` | `prompts/memory-agent/principles.md` | 证据先行、不存什么、沉默即不变、整批原子 | 中文化并去掉与本项目无关的仓库指令 |

## 未复用（附理由）

- **TeamHub / 多租户组织模型**：本项目是单用户陪伴场景，引入组织层只会增加一张无用的权限表。
- **FTS 内核 / SQLite 全文索引**：生产检索走 Qdrant + 后端 RAG 门面，端侧不需要第二套索引。
- **`apps/web` 前端与品牌资产**：许可 1.b 明确限制，且本项目客户端是 uni-app x。
- **本地模型栈（Pi / llama.cpp 配置）**：本项目模型统一走云端 API，端侧不部署推理。
- **Node sidecar**：已拍板不做；全部用 Python 等价移植，避免双运行时与打包复杂度。

## 声明

本目录下 `memory/ledger.py`、`agents/librarian.py`、`agents/sculptor.py`、`memory/projection.py`、
`prompts/sculptor.md` 含 Linger 衍生代码/文本，版权归 Linger 作者，按 Apache-2.0 使用；
修改部分版权归本项目。分发时保留本文件与源文件头部注释。
