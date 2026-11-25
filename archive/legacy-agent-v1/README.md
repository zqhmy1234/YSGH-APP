# Legacy Agent v1 (frozen)

**Status: archived, not imported, not tested.**

| 项 | 值 |
|---|---|
| 归档标签 | `legacy-agent-v1`（本仓库 git tag） |
| 归档日期 | 2026-11-23 |
| 原入口 | `agent/src/main.py`（LangGraph + Coze 去依赖版） |
| 替代入口 | `agent/src/main.py`（FastAPI + harness，端口 8400 本地 / 8300 staging） |
| 回滚 | `git checkout legacy-agent-v1 -- agent/src agent/tests` |

## 目录

```
src/                 # 旧 main / agents / tools / storage / web / utils / llm_provider / graphs
tests/               # 旧 v1 测试（pytest 不会收集；agent/pyproject.toml 的 testpaths 不含这里）
agentv3/             # 旧 agentv3 壳的空包 __init__ 与 README；实现已拆到 agent/src/*
README.md            # 本文件
```

## 规则

1. 归档目录不进入 `PYTHONPATH`，不参与 pytest；`agent/pyproject.toml` 的
   `testpaths` 只列 `tests/agentv3`、`tests/contract`、`tests/harness`。
2. 新 Agent 不得 import 本目录；`tests/contract/test_import_boundary.py` 用 AST
   断言源码中不存在 `agentv3` / 旧 `yishu` 的相对导入。
3. 旧 `agent/src/yishu/` 的安全 / 日志 / 降级经生产修补，已**移出**本归档到
   `agent/src/platform_support/`（语义保留），不随 v1 冻结。

## 为什么不删

保留一个发布周期的回滚能力：生产旧 8300 不受本机重构影响，staging 切流前若新
服务不可用，可 `git checkout legacy-agent-v1 -- agent/src` 临时回滚，再按
`docs/legacy-retirement.md` 走退役流程。
