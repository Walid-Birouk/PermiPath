"""Rules table: sourced figures, deadlines, fees and documents (build step 5).

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of the values in db/models.py at the time of this revision.
PERMITS = (
    "vls_ts_etudiant", "cst_etudiant", "csp_etudiant", "rece", "cst_salarie", "csp_salarie",
    "talent_salarie_qualifie", "stage_etudiant", "cr_etudiant", "cr_salarie", "cr_10_ans",
)  # fmt: skip
TOPICS = (
    "work_limit", "resources", "deadline", "fee", "duration", "salary_threshold",
    "residence_requirement", "internship", "documents",
)  # fmt: skip
SCOPES = ("all", "algerian")
STAGES = ("arriving", "renewing", "working", "finishing", "job_offer", "settling")
SOURCE_TYPES = ("law", "treaty", "fact_sheet", "prefecture", "case_law")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    op.create_table(
        "rules",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("permit", sa.Text, nullable=False),
        sa.Column("nationality_scope", sa.Text, nullable=False),
        sa.Column("topic", sa.Text, nullable=False),
        sa.Column("stage", sa.Text, nullable=False),
        sa.Column("label_fr", sa.Text, nullable=False),
        sa.Column("label_en", sa.Text, nullable=False),
        sa.Column("value", sa.Text, nullable=False),
        sa.Column("quote", sa.Text, nullable=False),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("source_id", sa.Text, nullable=False),
        sa.Column("source_title", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("source_checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_by", sa.Text),
        sa.Column("verified_at", sa.Date),
        sa.Column("loaded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(_in("permit", PERMITS), name="ck_rules_permit"),
        sa.CheckConstraint(_in("topic", TOPICS), name="ck_rules_topic"),
        sa.CheckConstraint(_in("nationality_scope", SCOPES), name="ck_rules_nationality_scope"),
        sa.CheckConstraint(_in("stage", STAGES), name="ck_rules_stage"),
        sa.CheckConstraint(_in("source_type", SOURCE_TYPES), name="ck_rules_source_type"),
        sa.CheckConstraint(
            "(verified_by IS NULL) = (verified_at IS NULL)", name="ck_rules_verification"
        ),
    )
    op.create_index("ix_rules_lookup", "rules", ["nationality_scope", "permit", "topic"])


def downgrade() -> None:
    op.drop_index("ix_rules_lookup", table_name="rules")
    op.drop_table("rules")
