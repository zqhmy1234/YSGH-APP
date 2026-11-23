# agentv3 — 五 Agent + 记忆账本 + 审判 Agent（单包落地）

> 目录形态：一个包 `agentv3`，依赖箭头只向内。`contracts` 最内，`ports` 次之，
> `agents/memory` 只依赖前两者，`adapters` 实现端口，`orchestrator` 是唯一组合根，
> `api` 只有一层薄路由，`judge` 只读代码与测试。

```
agent/src/agentv3/
├── contracts.py       冻结词汇表（pydantic, extra=forbid, frozen）
├── ports.py           12 个端口 Protocol + Span + 4 类 PortError
├── registry.py        5 个 AgentSpec（谁可写/谁可说/预算）
├── plan.py            纯函数 plan(ctx) —— 意图 → 调用表
├── frame.py           九段上下文 + 固定裁剪顺序
├── obs.py             redact / Tracer / Degradation / CostMeter
├── orchestrator.py    单轮组合根（唯一 import 全部 agents 的地方）
├── agents/            muse librarian provenance sculptor serendipity
├── memory/            ledger（双时间/幂等/回滚）policy（边界与危机）projection（digest）
├── adapters/          fakes（测试替身）· backend（HTTP）· sqlite_memory（持久化）
├── jobs/              nightly（Sculptor 批次，幂等）
├── api/               FastAPI 路由（turn/digest/memory/purge/health + /v1/chat 兼容）
├── prompts/           五个提示词资产，按名加载
└── judge/             checks（静态）· mutation（变异）· run（裁决）· evidence（逐模块说明）
```

## 五条硬规矩（由测试与审判双重看守）

1. **只有 Muse 说话**：其他 Agent 返回结构化结果，不产出面向用户的话术。
2. **只有 Sculptor 写记忆**：Muse/Librarian/Provenance/Serendipity 均 `may_write=False`；写入必须走批次。
3. **用户修正 > AI**：`propose` 拒、`commit` 整批回滚、`frame` 不裁修正段、`Provenance` 判 reject，四处闭环。
4. **降级必须诚实**：端口失败 → `partial` + `gaps`，绝不静默 `empty`；LLM 失败 → 固定降级话术。
5. **契约漂移即报错**：`extra=forbid` + `frozen`；新增字段必须改 contracts 并过测试。

## 本地运行

```bash
cd agent
PYTHONPATH=src /tmp/ghvenv/bin/pytest tests/agentv3 tests/contract -q   # 195 项
PYTHONPATH=src /tmp/ghvenv/bin/python -m agentv3.judge.run       # 静态 + 套件 + 10 变异
```

HTTP（依赖真实端口时）：

```bash
cd agent && PYTHONPATH=src /tmp/ghvenv/bin/python -c \
  "from agentv3.api.routes import app_from_env; import uvicorn; uvicorn.run(app_from_env(), port=8301)"
```

## 新增一个 Agent 的最小改动

1. 在 `contracts.AgentName` 加枚举；2. 在 `registry.SPECS` 加一条（写/说/预算）；
3. 新建 `agents/<name>.py`（只 import contracts/ports）；4. `plan.py` 决定何时调用；
5. 在 `orchestrator.py` 接线一行；6. 加 `tests/agentv3/test_<name>.py`；
7. 在 `judge/evidence.py` 登记输入/输出与有效性；8. 跑 judge，变异必须被杀。

不需要动其它 Agent，也不允许动。
