"""数据层客户端入口 —— 2026-09-23（AG3）**已由本地 SQLAlchemy 替换**。

历史：上游此文件从 Coze workload identity 取 `COZE_SUPABASE_*` 凭据、经 httpx 建 Supabase
（PostgREST）客户端并注入 Coze 埋点传输层（见 agent/UPSTREAM.md 四-3/四-9）。
现状：用户拍板数据落**我们自己的 Postgres**，实现改为 `storage/database/local_client.py`
（PostgREST 兼容薄层，SQLAlchemy 支撑）。

**为什么保留同名函数**：上游 25+ 处业务调用（`tools/memory_tools.py`、`tools/url_fetch_tools.py`、
`web/api_routes.py`、`services/{wechat,daily_review}_service.py`）都经 `get_supabase_client()`
单入口取客户端；保名即可**零改动**切换到本地数据层，把风险集中在薄层单文件。
新代码请用 `get_client()`；`get_supabase_client` 仅作兼容别名保留（更名收口见 UPSTREAM 六·AG4）。
"""
from __future__ import annotations

from typing import Any

from storage.database.local_client import LocalClient, create_local_client


def get_client(*_args: Any, **_kwargs: Any) -> LocalClient:
    """取本地数据层客户端（签名容忍上游的 `token=` 等实参，一律忽略）。"""
    return create_local_client()


def get_supabase_client(token: str | None = None) -> LocalClient:  # 兼容别名
    """**兼容别名**（上游调用点仍在用）—— 语义等价于 `get_client()`，`token` 被忽略。"""
    return get_client(token=token)
