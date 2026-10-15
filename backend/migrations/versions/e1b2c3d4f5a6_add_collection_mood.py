"""补 `knowledge_collections.mood` —— 修「带情绪的收藏必然保存失败」

Revision ID: e1b2c3d4f5a6
Revises: d9a0b1c2d3e4
Create Date: 2026-10-06 00:00:00.000000

## 为什么需要这条迁移

`save_knowledge_collection`（`agent/src/tools/memory_tools.py`）在 `validated["mood"]`
非空时写 `data["mood"]`，而 `knowledge_collections` **从来没有 mood 列**——
三条证据：（a）本仓迁移 `a4b5c6d7e8f9` 建表时未含该列；（b）agent ORM
`agent/src/storage/database/shared/model.py::KnowledgeCollections` 未声明；
（c）后端镜像 ORM `app/db/models/memory_agent.py::KnowledgeCollection` 未声明。
实测连库确认（`information_schema.columns`）确无该列。

后果：LLM 只要给收藏打一个情绪标签（工具签名与 docstring 都允许，且
`config/agent_llm_config.json` 的 SOP 明确「L2 情绪：记忆库必打，**收藏库可选**」），
写入就会抛 `ValueError("表 knowledge_collections 上不存在列 'mood'")` ⇒
**带情绪的收藏 100% 保存失败**，且模型可能反复重试（与历史上单轮 32~66s 的
工具循环同根因族）。

## 为什么是「补列」而不是「砍掉 mood 写入」

标签体系 SOP（`agent/src/tools/tag_rules.py:8`）把 L2 情绪定义为**收藏库可选**，
即这是**设计内**的能力，只是建表时漏了列。补一个 nullable 列是纯增量、
不影响任何存量行，比删掉一个已文档化的能力更正确。

## 两条建库路径

`memories` / `knowledge_collections` / `memory_categories` 三表**只由本迁移链创建**
（`backend/sql/schema.sql` 里没有它们，已核实），故本迁移只需改 alembic 一侧，
无需同步 `schema.sql`。

## 两处 ORM 必须同时改（否则 autogenerate 会生成 DROP COLUMN）

`a4b5c6d7e8f9` 的文件头已就此警告过：agent 的 ORM 与后端镜像 ORM 是同一份
schema 的两处声明，本迁移同步补了：
  · `agent/src/storage/database/shared/model.py::KnowledgeCollections.mood`
  · `backend/app/db/models/memory_agent.py::KnowledgeCollection.mood`
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1b2c3d4f5a6"
down_revision: str | Sequence[str] | None = "d9a0b1c2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """加一列（幂等：`IF NOT EXISTS`，重复跑不报错）。

    宽度取 `varchar(20)` 与 `memories.mood` 一致——同一套八选一中文情绪枚举，
    两表口径不该有差异。
    """
    op.execute(
        "ALTER TABLE knowledge_collections "
        "ADD COLUMN IF NOT EXISTS mood varchar(20)"
    )
    op.execute(
        "COMMENT ON COLUMN knowledge_collections.mood IS 'L2 情绪状态（八选一，收藏库可选）'"
    )


def downgrade() -> None:
    """回滚 = 删列。注意：**会丢掉已写入的收藏情绪**（这是数据损失，不是纯结构回滚）。"""
    op.execute("ALTER TABLE knowledge_collections DROP COLUMN IF EXISTS mood")
