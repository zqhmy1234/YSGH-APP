"""`memories` 幂等键：content_hash + 时间桶 + 部分唯一索引（关掉判重 TOCTOU）

Revision ID: f2c3d4e5a6b7
Revises: e1b2c3d4f5a6
Create Date: 2026-10-06 00:00:00.000000

## 修的是什么

`save_memory` 的判重与写入**分属两个事务**：

    SELECT 判重（事务1，自动提交） → INSERT（事务2）

并发下两个同内容请求都会在 SELECT 阶段看到"不存在"，然后各写一条。
实测证据：库里 `test-idempot…` 用户存在**同内容 4 条**（同一天），
`ag4-smoke` 存在 2 条——TOCTOU 不是理论风险。

## 为什么**不能**用 `UNIQUE(user_id, content)`

这是本轮否决的一个方案，三条硬事实（均实测）：

1. **加不上去**：库里已有同 user 同内容的重复行 ⇒ `CREATE UNIQUE INDEX` 直接
   失败（存量数据冲突），迁移会中断。
2. **时间窗唯一索引在 Postgres 不可能**：索引谓词必须 IMMUTABLE，而
   `WHERE created_at > now() - interval '10 minutes'` 用 `now()`（STABLE）⇒
   `InvalidObjectDefinition: 谓词函数 now() 不是 IMMUTABLE`。
3. **会破坏合法业务**：用户隔 20 天再记一次「今天天气不错」是完全正常的，
   全局唯一会让这条**永远写不进去**（`UniqueViolation` 已复现）。

## 所以用「哈希 + 固定时间桶」

    content_hash  = sha256(归一化内容的**前 50 字**)
    dedup_bucket  = int(epoch_seconds // (窗口分钟 × 60))
    部分唯一索引   = (user_id, content_hash, dedup_bucket) WHERE 两者均非 NULL

三个设计取舍，逐条说明：

· **哈希前 50 字**（`DEDUP_PREFIX_LEN`，与 `memory_tools` / `output_validator`
  同口径）：LLM 重试时是把内容**变长**（尾部追加时间/语气词），全内容哈希抓不到，
  前缀哈希才能抓住——这正是当初判重方向写反的那个坑。
· **时间桶而非常量**：桶随窗口推进，旧的重复行落进旧桶、不再参与冲突判定
  ⇒ **不会永久封禁**「以后再记一次同样的话」。这是与方案 1 的关键差别。
· **固定桶的固有边界**：跨桶边界的两次写入（相差 1 秒但分属两个桶）不会被 DB 拦下。
  Python 层的时间窗判重仍能覆盖这种情形（它回看最近 N 分钟的行）；
  DB 约束的职责只是**关掉并发竞态**这个窗口。两端职责不同，不是重复实现。
· **只对非 NULL 生效**（部分索引）：存量行 `content_hash IS NULL` ⇒ 不出现在索引里，
  迁移对历史数据零影响。

## 窗口口径

桶宽取自 `AGENT_DEDUP_WINDOW_MIN`（默认 10 分钟）。**运行时改这个环境变量会让
新旧桶不连续**（同一时刻算出的桶号不同），跨改动的去重会失效一次——
这是可接受的，但改动者应当知道。Python 层判重不受影响。

## 两处 ORM 必须同时改

`a4b5c6d7e8f9` 文件头警告过：属同一份 schema 的两处声明，本迁移同步补了
`agent/src/storage/database/shared/model.py::Memories` 与
`backend/app/db/models/memory_agent.py::Memory`。漏改任一处，
`alembic revision --autogenerate` 会生成 `DROP COLUMN`。
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f2c3d4e5a6b7"
down_revision: str | Sequence[str] | None = "e1b2c3d4f5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "uq_memories_user_hash_bucket"


def upgrade() -> None:
    """加两列 + 部分唯一索引（全部幂等，重复跑收敛到同一目标态）。"""
    op.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS content_hash varchar(64)")
    op.execute("ALTER TABLE memories ADD COLUMN IF NOT EXISTS dedup_bucket integer")
    # ⚠️ 注释文本必须与两处 ORM 的 `comment=` **逐字一致**，否则 `alembic check` 会报
    # `modify_comment` 漂移（实测已复现：我原先在这里多写了"与 DEDUP_PREFIX_LEN 同口径"
    # 与"epoch_seconds"，而 ORM 上没有，于是 check 要求改注释）。
    # 注释是给人和 autogenerate 两边看的，同一条信息不该有两个版本。
    op.execute(
        "COMMENT ON COLUMN memories.content_hash IS "
        "'归一化内容前50字的sha256（判重幂等键）'"
    )
    op.execute(
        "COMMENT ON COLUMN memories.dedup_bucket IS "
        "'判重时间桶=epoch//(窗口分钟×60)'"
    )
    # 部分唯一索引：只约束「两个键都有值」的行 ⇒ 存量数据（NULL）零影响，
    # 且不引入"永久唯一"（桶会推进）。
    op.execute(
        f"CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX} "
        "ON memories (user_id, content_hash, dedup_bucket) "
        "WHERE content_hash IS NOT NULL AND dedup_bucket IS NOT NULL"
    )


def downgrade() -> None:
    """回滚 = 删索引与两列。**会丢掉幂等键**（可从 content 重算，非不可逆损失）。"""
    op.execute(f"DROP INDEX IF EXISTS {_INDEX}")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS dedup_bucket")
    op.execute("ALTER TABLE memories DROP COLUMN IF EXISTS content_hash")
