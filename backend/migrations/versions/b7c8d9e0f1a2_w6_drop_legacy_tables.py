"""W6 清理：DROP 11 张零引用 legacy 表（建表先行、代码未落地的残留）

Revision ID: b7c8d9e0f1a2
Revises: f2c3d4e5a6b7
Create Date: 2026-10-08 00:00:00.000000

## 为什么要删

这 11 张表由 baseline 迁移（431bcaa8bd54）+ schema.sql 双路径建表，属于
「设计过但从未落地」的功能残留（B1-6 审计、B5-a 分段/关怀节流、B5-b 骨架池/
护栏、成本归因、v3 标签域等）——**实际功能全部走了别的实现**：

- 护栏 → content_safety + sensitive_words + pipeline_ext/sensitive.py
- 审计 → deleted_logs / event_edit_log
- 标签 → contents.tags_json + Qdrant payload tags + extra.ci_tags
- 语音 → ASR 全文转写（无分段）

全仓（backend/app + backend/tests + scripts）大小写不敏感检索：
除 backend/tests/conftest.py 的清理 DELETE 与本守卫白名单外**零代码引用**。
真库实测（2026-10-08）：11 张表**全部 0 行**，DROP 无数据风险。

## 有意保留的两张无模型表（见 tests/test_schema_drift_guard.py 白名单）

- `profile_l2_evidence`：业务活着（profile_annotator.py 写 / profile_admin.py 清），
  profile_admin.py:68-69 已拍板「无 ORM ⇒ 清除同样走 raw SQL」
- `finetune_jobs`：外部进程 scripts/reflow_global.py（B5-c 纠错回流）的记录载体，
  其触发计划未拍板前不动

## 幂等性

`DROP TABLE IF EXISTS`：对已应用过的库重放安全；降级路径不提供
（这批表无数据、无代码，回滚即重建只会复活垃圾——如确需回滚，从
baseline 迁移或 schema.sql 历史版本取 DDL）。
"""
from alembic import op

revision = "b7c8d9e0f1a2"
down_revision = "f2c3d4e5a6b7"
branch_labels = None
depends_on = None

# 11 张零引用 legacy 表。⚠️ 顺序有讲究：event_tags / content_tags 带 FK 指向 tags，
# question_history 带 FK 指向 question_templates——**必须先删子表再删被引用表**
# （首跑先删 tags 被 event_tags_tag_id_fkey 挡住，实测踩坑）。保留表（users/
# contents/events）无 FK 指向本批任何一张。
LEGACY_TABLES = (
    "content_tags",
    "event_tags",
    "voice_segments",
    "question_history",
    "question_templates",
    "guardrail_logs",
    "audit_log",
    "ai_request_logs",
    "api_cost_stats",
    "app_settings",
    "tags",
)


def upgrade() -> None:
    for table in LEGACY_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}")


def downgrade() -> None:
    # 有意不实现：这些表无数据无代码，重建只会复活垃圾。见文件头说明。
    pass
