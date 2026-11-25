"""Replace the five persona dimensions with three relationship choices.

Revision ID: e7b8c9d0e1f2
Revises: f6a7b8c9d0e1

The old columns stay for rollback and historical rows. New columns store the
0/1/2 positions behind full-sentence choices; the API never exposes the numbers.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "f6a7b8c9d0e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_ai_persona",
        sa.Column("initiative", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "user_ai_persona",
        sa.Column("density", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
    )
    op.add_column(
        "user_ai_persona",
        sa.Column("advice_bias", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
    )
    op.create_check_constraint("ck_persona_initiative", "user_ai_persona", "initiative BETWEEN 0 AND 2")
    op.create_check_constraint("ck_persona_density", "user_ai_persona", "density BETWEEN 0 AND 2")
    op.create_check_constraint("ck_persona_advice_bias", "user_ai_persona", "advice_bias BETWEEN 0 AND 2")

    # Map legacy five-dimension rows to the closest full-sentence choice.
    op.execute(
        """
        UPDATE user_ai_persona
        SET initiative = 1,
            density = CASE
                WHEN verbosity <= 2 THEN 0
                WHEN verbosity = 3 THEN 1
                ELSE 2
            END,
            advice_bias = CASE
                WHEN advice = 0 THEN 0
                WHEN advice = 1 THEN 1
                ELSE 2
            END
        """
    )


def downgrade() -> None:
    op.drop_constraint("ck_persona_advice_bias", "user_ai_persona", type_="check")
    op.drop_constraint("ck_persona_density", "user_ai_persona", type_="check")
    op.drop_constraint("ck_persona_initiative", "user_ai_persona", type_="check")
    op.drop_column("user_ai_persona", "advice_bias")
    op.drop_column("user_ai_persona", "density")
    op.drop_column("user_ai_persona", "initiative")
