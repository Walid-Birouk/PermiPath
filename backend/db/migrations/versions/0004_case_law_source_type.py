"""Source type `case_law`: court decisions published on Légifrance.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of the values in db/models.py at the time of this revision.
OLD_SOURCE_TYPES = ("law", "treaty", "fact_sheet", "prefecture")
SOURCE_TYPES = (*OLD_SOURCE_TYPES, "case_law")
SOURCE_TYPE_CHECKS = {"chunks": "ck_chunks_source_type", "changelog": "ck_changelog_source_type"}


def _set_constraints(source_types: tuple[str, ...]) -> None:
    values = "(" + ", ".join(f"'{s}'" for s in source_types) + ")"
    for table, check in SOURCE_TYPE_CHECKS.items():
        op.drop_constraint(check, table, type_="check")
        op.create_check_constraint(check, table, f"source_type IN {values}")


def upgrade() -> None:
    _set_constraints(SOURCE_TYPES)


def downgrade() -> None:
    """Deletes `case_law` rows (chunks, their versions and changelog entries) first."""
    op.execute("DELETE FROM chunk_versions WHERE source_type = 'case_law'")
    op.execute("DELETE FROM chunks WHERE source_type = 'case_law'")
    op.execute("DELETE FROM changelog WHERE source_type = 'case_law'")
    _set_constraints(OLD_SOURCE_TYPES)
