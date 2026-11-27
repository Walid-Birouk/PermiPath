"""Journey stage `settling`: long-term residence after the job offer.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of the values in db/models.py at the time of this revision.
OLD_STAGES = ("arriving", "renewing", "working", "finishing", "job_offer")
STAGES = (*OLD_STAGES, "settling")


def _in(stages: tuple[str, ...]) -> str:
    return "(" + ", ".join(f"'{s}'" for s in stages) + ")"


def _array(stages: tuple[str, ...]) -> str:
    return "ARRAY[" + ", ".join(f"'{s}'" for s in stages) + "]::text[]"


def _set_constraints(stages: tuple[str, ...]) -> None:
    op.drop_constraint("ck_chunks_stages", "chunks", type_="check")
    op.create_check_constraint("ck_chunks_stages", "chunks", f"journey_stages <@ {_array(stages)}")
    op.drop_constraint("ck_query_logs_stage", "query_logs", type_="check")
    op.create_check_constraint(
        "ck_query_logs_stage",
        "query_logs",
        f"journey_stage IS NULL OR journey_stage IN {_in(stages)}",
    )


def upgrade() -> None:
    _set_constraints(STAGES)


def downgrade() -> None:
    """Removes `settling` from stored rows before restoring the old constraints."""
    for table in ("chunks", "chunk_versions"):
        op.execute(f"UPDATE {table} SET journey_stages = array_remove(journey_stages, 'settling')")
    op.execute("UPDATE query_logs SET journey_stage = NULL WHERE journey_stage = 'settling'")
    _set_constraints(OLD_STAGES)
