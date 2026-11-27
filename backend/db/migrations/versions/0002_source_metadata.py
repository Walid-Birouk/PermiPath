"""Source metadata for precedence and filtering: new source types, authority, nationality scope,
prefecture.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of the values in db/models.py at the time of this revision.
OLD_SOURCE_TYPES = "('ceseda_article', 'sp_sheet', 'anef_guide')"
SOURCE_TYPES = "('law', 'treaty', 'fact_sheet', 'prefecture')"
AUTHORITIES = "('official', 'secondary')"
NATIONALITY_SCOPES = "('all', 'algerian')"

# Old value -> new value. `anef_guide` was never populated; ANEF guides are fact sheets.
UPGRADE_MAP = {"ceseda_article": "law", "sp_sheet": "fact_sheet", "anef_guide": "fact_sheet"}
DOWNGRADE_MAP = {"law": "ceseda_article", "fact_sheet": "sp_sheet"}

CONTENT_TABLES = ("chunks", "chunk_versions")
SOURCE_TYPE_CHECKS = {"chunks": "ck_chunks_source_type", "changelog": "ck_changelog_source_type"}


def _remap(table: str, mapping: dict[str, str]) -> None:
    cases = " ".join(f"WHEN '{old}' THEN '{new}'" for old, new in mapping.items())
    op.execute(f"UPDATE {table} SET source_type = CASE source_type {cases} ELSE source_type END")


def upgrade() -> None:
    for table, check in SOURCE_TYPE_CHECKS.items():
        op.drop_constraint(check, table, type_="check")
    for table in (*CONTENT_TABLES, "changelog"):
        _remap(table, UPGRADE_MAP)
    for table, check in SOURCE_TYPE_CHECKS.items():
        op.create_check_constraint(check, table, f"source_type IN {SOURCE_TYPES}")

    for table in CONTENT_TABLES:
        op.add_column(
            table, sa.Column("authority", sa.Text, nullable=False, server_default="official")
        )
        op.add_column(
            table, sa.Column("nationality_scope", sa.Text, nullable=False, server_default="all")
        )
        op.add_column(table, sa.Column("prefecture", sa.Text))

    op.create_check_constraint("ck_chunks_authority", "chunks", f"authority IN {AUTHORITIES}")
    op.create_check_constraint(
        "ck_chunks_nationality_scope", "chunks", f"nationality_scope IN {NATIONALITY_SCOPES}"
    )
    op.create_index("ix_chunks_filters", "chunks", ["nationality_scope", "source_type"])


def downgrade() -> None:
    """Lossy: `treaty` and `prefecture` rows have no equivalent before this revision and are
    deleted."""
    op.drop_index("ix_chunks_filters", table_name="chunks")
    op.drop_constraint("ck_chunks_nationality_scope", "chunks", type_="check")
    op.drop_constraint("ck_chunks_authority", "chunks", type_="check")
    for table in CONTENT_TABLES:
        op.drop_column(table, "prefecture")
        op.drop_column(table, "nationality_scope")
        op.drop_column(table, "authority")

    for table, check in SOURCE_TYPE_CHECKS.items():
        op.drop_constraint(check, table, type_="check")
    for table in (*CONTENT_TABLES, "changelog"):
        op.execute(f"DELETE FROM {table} WHERE source_type IN ('treaty', 'prefecture')")
        _remap(table, DOWNGRADE_MAP)
    for table, check in SOURCE_TYPE_CHECKS.items():
        op.create_check_constraint(check, table, f"source_type IN {OLD_SOURCE_TYPES}")
