"""Usage counters for the per-visitor daily question limit.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "usage_counters",
        sa.Column("day", sa.Date, primary_key=True),
        sa.Column("visitor", sa.Text, primary_key=True),
        sa.Column("questions", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_table("usage_counters")
