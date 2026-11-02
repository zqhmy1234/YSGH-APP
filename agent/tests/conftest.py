"""Agent 测试的**环境隔离**入口（此前缺失，导致测试结论取决于开发机 shell 状态）。

## 为什么必须有这个文件

Agent 侧的配置一律用 `load_dotenv(..., override=False)`，语义是**进程环境变量优先于 .env**。
这对运行期是对的（运维用环境变量覆盖 .env），但对测试是灾难：本机 shell 里长期残留一套
陈旧的系统级 `DASHSCOPE_API_KEY`（旧的 `sk-sp-` Token Plan key），于是

    进程环境的 sk-sp- key  +  .env 里的 DASHSCOPE_WORKSPACE_ID

被拼成一份**从未有人写过的组合**：token-plan 专属端点 + `X-DashScope-WorkSpace` 头。
百炼直接拒：

    400 The X-DashScope-WorkSpace header value must match the workspace
        bound to the API key.

而同一个测试在"干净 shell"（CI、别人的机器）里是绿的 —— 也就是**同一份代码，测试结果
取决于跑它的那台机器当时继承了什么环境变量**。这类不稳定无法通过读代码发现，必须在
测试入口处钉死。

## 口径

与 `agent/scripts/local_run.ps1`（启动前清 `DASHSCOPE_*`）**保持一致**：进程继承来的
`DASHSCOPE_*` 一律丢弃，`agent/.env` 是唯一真相源。
conftest.py 在 pytest **collection 阶段**执行，早于各测试模块的 import，因此这里的
清理对 `import llm_provider` 时构造的 settings 同样生效。

⚠️ 刻意**只清 DASHSCOPE_***，不清其他变量（如 DATABASE_URL）：前者是本机陈旧残留的
高发区且清掉无副作用，后者清掉反而会让集成测试失去真实数据库连接。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

AGENT_ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = AGENT_ROOT / ".env"

# 本机/父进程继承来的百炼变量一律丢弃（口径同 scripts/local_run.ps1）
_STALE_PREFIX = "DASHSCOPE_"
_dropped = sorted(k for k in os.environ if k.startswith(_STALE_PREFIX))
for _k in _dropped:
    del os.environ[_k]

load_dotenv(ENV_FILE, override=False)
