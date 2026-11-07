"""merge heads: local + origin/develop(B-1) 2026-10-15

Revision ID: 6a12e10c4868
Revises: b7c8d9e0f1a2, e1b1c1d1e1f1
Create Date: 2026-10-15 01:51:05.332625

"""
from typing import Sequence, Union



# revision identifiers, used by Alembic.
revision: str = '6a12e10c4868'
down_revision: Union[str, Sequence[str], None] = ('b7c8d9e0f1a2', 'e1b1c1d1e1f1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
