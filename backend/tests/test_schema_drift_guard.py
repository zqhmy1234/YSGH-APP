"""Schema 漂移守卫：防止 `remove_table` 清单静默增长（W6 · 2026-10-06）

## 为什么必须有这个测试

`alembic check` 实测报 **26 处 `remove_table`**：`question_history`、`guardrail_logs`、
`audit_log`、`content_tags`、`profile_l2_evidence`、`finetune_jobs`、`question_templates`
等表**在库里存在但 ORM 无模型**。

风险是**真实且累积的**：任何人跑 `alembic revision --autogenerate`，autogenerate
都会把"库里有、metadata 没有"判为多余而生成 **DROP TABLE**——本轮补
`knowledge_collections.mood` 时就亲眼看到它想删刚建的索引（同理，只是对象是表）。
若这些表里有真实业务数据，一次手滑就是不可逆丢失。

## 本测试守什么

**不是**要求漂移为零（那需要给 26 张表补模型或逐表判定 legacy，见
docs/功能对齐计划_20261006.md W6），而是锁住**已知清单**：

- 新增一张"库里有、ORM 没有"的表 ⇒ 本测试红（提醒先补模型或登记）；
- 已知清单里的表被补了模型 ⇒ 清单要同步删（否则本测试红，提醒清单过期）。

也就是说它把"漂移在悄悄累积"变成"漂移每增加一张就有人必须表态"。

## 运行条件

需要真实 Postgres + alembic（连不上则 skip，不产生假红）。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine

# 已知漂移（**改动本清单必须说明原因**）：新增 = 库里新出现无模型表；
# 移除 = 已补模型或已确认清理。2026-10-08 W6：11 张零引用 legacy 表
# （question_history/question_templates/guardrail_logs/audit_log/ai_request_logs/
# api_cost_stats/app_settings/tags/event_tags/content_tags/voice_segments）
# 已随迁移 DROP，清单从 13 收敛到 2。剩下两张**有意保留无模型**：
#   · profile_l2_evidence —— 业务活着（profile_annotator 写 / profile_admin 清），
#     profile_admin.py:68-69 已拍板「无 ORM ⇒ 清除同样走 raw SQL」
#   · finetune_jobs —— 外部进程 reflow_global.py（B5-c 纠错回流）的记录载体
# 2026-10-15（合并 origin/develop 的 B-1 后）：**清单清空**——原先这两张"**有意无模型**"的
# 裸 SQL 表（本仓 2026-10-10 的决策，见 `backend/sql/schema.sql` 头部与台账）已被上游 B-1
# 补上 ORM 模型（`app/db/models/finetune.py:11 FinetuneJob`、
# `app/db/models/profile.py:91 ProfileL2Evidence`）⇒ **不再构成漂移**。
# 清空后本测试的语义变成"库里不存在任何无模型表"= 理想态 ✓。
# ⚠️ 将来若再出现"有意无模型"的表，必须在此写明理由与销项触发（本清单即豁免池）。
KNOWN_DRIFT_TABLES: set[str] = set()


@pytest.fixture(scope="module")
def remove_tables() -> set[str]:
    """用 alembic 的 autogenerate API 直接取 diff（不解析文本输出）。"""
    import app.db.models  # noqa: F401 —— **必须先注册全部模型到 Base.metadata**
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext

    #   漏掉这行，compare_metadata 会把「模型存在但未 import」的三张 agent 表
    #   判成 remove_table（本测试首跑就栽在这，见下方 agent 三表断言的注释）。
    #   migrations/env.py:22 也是这么做的。
    from app.core.config import settings
    from app.db.session import Base

    try:
        engine = create_engine(settings.database_url, pool_pre_ping=True)
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn)
            diff = compare_metadata(ctx, Base.metadata)
    except Exception as exc:  # noqa: BLE001 —— 环境性缺失不是代码缺陷
        pytest.skip(f"连不上数据库或 alembic 不可用，跳过漂移守卫：{type(exc).__name__}")

    names: set[str] = set()
    for op in diff:
        # op 形如 ('remove_table', Table(...)) / ('remove_index', Index(..., table=...))
        if op[0] == "remove_table":
            names.add(op[1].name)
    return names


def test_no_new_untracked_tables(remove_tables):
    """库里的无模型表 ⊆ 已知清单。多出来的 ⇒ 先补 ORM 模型或登记。"""
    unknown = remove_tables - KNOWN_DRIFT_TABLES
    assert not unknown, (
        f"发现 {len(unknown)} 张「库里有、ORM 无」的新表，autogenerate 会生成 DROP TABLE："
        f"{sorted(unknown)}\n"
        "处理：① 给它们补 ORM 模型（app/db/models/），或 ② 确认是 legacy 并登记进本测试的 "
        "KNOWN_DRIFT_TABLES。两选一，不许静默放过。"
    )


def test_known_drift_list_has_no_stale_entries(remove_tables):
    """已知清单里已消失的表要移除（清单过期会让守卫失去意义）。"""
    stale = KNOWN_DRIFT_TABLES - remove_tables
    assert not stale, (
        f"已知漂移清单里有 {len(stale)} 张表已经不在漂移里了（可能已补模型）：{sorted(stale)}\n"
        "请把它们从 KNOWN_DRIFT_TABLES 移除，保持清单与真实漂移一致。"
    )


def test_agent_memory_tables_are_not_in_drift(remove_tables):
    """agent 三表绝不允许出现在漂移里（它们是本轮对齐的核心资产）。

    ⚠️ 注意：本测试首跑时这里**真的红了**——原因不是库或模型有问题，
    而是本文件当时漏了 `import app.db.models`（见 fixture 注释），导致
    compare_metadata 的 metadata 里根本没有这三张表，于是被判成
    remove_table。这正是 a4b5c6d7e8f9 文件头警告的失效形态：
    **模型没被 import ≠ 模型不存在**，两者在 autogenerate 眼里是同一回事。
    """
    protected = {"memories", "knowledge_collections", "memory_categories"}
    leaked = protected & remove_tables
    assert not leaked, (
        f"agent 记忆域表出现在 remove_table 里：{sorted(leaked)} —— "
        "autogenerate 将生成 DROP TABLE。先检查本测试是否 import 了 app.db.models，"
        "再检查 app/db/models/__init__.py 是否仍导入 memory_agent（见 a4b5c6d7e8f9 文件头）。"
    )
