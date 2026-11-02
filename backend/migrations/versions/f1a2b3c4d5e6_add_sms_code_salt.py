"""G1/R6#9（认证安全）：sms_codes 验证码哈希加盐

- 新增 sms_codes.salt（每码随机盐，随行落库；code 列改存 SHA-256(salt:code) 哈希）
- 存量无盐行（salt 为空）由校验侧走无盐 SHA-256 兼容分支（无需回填迁移）——
  验证码 5 分钟有效，存量码到期后自然失效，不阻塞新码写入。
- devices.refresh_token_hash 已由 d3e4f5a6b7c8 加列；G1/R6#8 哈希算法升级为
  HMAC-SHA256+独立密钥属纯应用层变更（带 `hmac$` 前缀，无 DDL 变更）。

幂等：add_column 本身幂等（重复执行报列已存在则由 alembic 版本表防重）；downgrade 对称删除。

Revision ID: f1a2b3c4d5e6
Revises: e5f6a7b8c9d0
Create Date: 2026-08-27
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | Sequence[str] | None = "e5f6a7b8c9d0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sms_codes", sa.Column("salt", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("sms_codes", "salt")
