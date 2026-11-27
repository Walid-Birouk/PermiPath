"""Initial schema: chunks, chunk_versions, changelog, query_logs.

Revision ID: 0001
Revises:
Create Date: 2026-11-25
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Frozen copies of the values in db/models.py at the time of this revision.
EMBEDDING_DIM = 1024
SOURCE_TYPES = "('ceseda_article', 'sp_sheet', 'anef_guide')"
LANGS = "('fr', 'en')"
STAGES_IN = "('arriving', 'renewing', 'working', 'finishing', 'job_offer')"
STAGES_ARRAY = "ARRAY['arriving', 'renewing', 'working', 'finishing', 'job_offer']::text[]"
TSV_EXPRESSION = (
    "to_tsvector(CASE lang WHEN 'en' THEN 'english'::regconfig ELSE 'french'::regconfig END, "
    "coalesce(title, '') || ' ' || text)"
)

TS = sa.DateTime(timezone=True)
TEXT_ARRAY = sa.ARRAY(sa.Text)


def _content_columns() -> list[sa.Column[object]]:
    """Columns shared by `chunks` and `chunk_versions`."""
    return [
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("source_id", sa.Text, nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("url", sa.Text, nullable=False),
        sa.Column("text", sa.Text, nullable=False),
        sa.Column("lang", sa.Text, nullable=False),
        sa.Column("journey_stages", TEXT_ARRAY, nullable=False, server_default="{}"),
        sa.Column("procedure_tags", TEXT_ARRAY, nullable=False, server_default="{}"),
        sa.Column("in_force_from", sa.Date),
        sa.Column("in_force_to", sa.Date),
        sa.Column("source_updated_at", TS),
        sa.Column("fetched_at", TS, nullable=False),
        sa.Column("content_hash", sa.Text, nullable=False),
    ]


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "chunks",
        sa.Column("id", sa.Text, primary_key=True),
        *_content_columns(),
        sa.Column("embedding", Vector(EMBEDDING_DIM)),
        sa.Column("tsv", TSVECTOR, sa.Computed(TSV_EXPRESSION, persisted=True)),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("source_type", "source_id", "chunk_index", name="uq_chunks_source_pos"),
        sa.CheckConstraint(f"source_type IN {SOURCE_TYPES}", name="ck_chunks_source_type"),
        sa.CheckConstraint(f"lang IN {LANGS}", name="ck_chunks_lang"),
        sa.CheckConstraint(f"journey_stages <@ {STAGES_ARRAY}", name="ck_chunks_stages"),
    )
    op.create_index("ix_chunks_tsv", "chunks", ["tsv"], postgresql_using="gin")
    op.create_index(
        "ix_chunks_journey_stages", "chunks", ["journey_stages"], postgresql_using="gin"
    )
    op.create_index(
        "ix_chunks_embedding",
        "chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "chunk_versions",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("chunk_id", sa.Text, nullable=False),
        *_content_columns(),
        sa.Column("superseded_at", TS, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("chunk_id", "content_hash", name="uq_chunk_versions"),
    )
    op.create_index("ix_chunk_versions_chunk_id", "chunk_versions", ["chunk_id"])

    op.create_table(
        "changelog",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("source_type", sa.Text, nullable=False),
        sa.Column("source_id", sa.Text, nullable=False),
        sa.Column("chunk_id", sa.Text),
        sa.Column("change_type", sa.Text, nullable=False),
        sa.Column("old_hash", sa.Text),
        sa.Column("new_hash", sa.Text),
        sa.Column("summary", sa.Text),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("detected_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_at", TS),
        sa.CheckConstraint(f"source_type IN {SOURCE_TYPES}", name="ck_changelog_source_type"),
        sa.CheckConstraint(
            "change_type IN ('added', 'modified', 'removed')", name="ck_changelog_change_type"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'reviewed', 'rejected')", name="ck_changelog_status"
        ),
    )
    op.create_index("ix_changelog_status_detected", "changelog", ["status", "detected_at"])

    op.create_table(
        "query_logs",
        sa.Column("id", sa.BigInteger, sa.Identity(), primary_key=True),
        sa.Column("created_at", TS, nullable=False, server_default=sa.func.now()),
        sa.Column("journey_stage", sa.Text),
        sa.Column("retrieved_chunk_ids", TEXT_ARRAY, nullable=False, server_default="{}"),
        sa.Column("latency_ms", sa.Integer),
        sa.Column("prompt_tokens", sa.Integer),
        sa.Column("completion_tokens", sa.Integer),
        sa.Column("eval_flags", JSONB, nullable=False, server_default="{}"),
        sa.CheckConstraint(
            f"journey_stage IS NULL OR journey_stage IN {STAGES_IN}", name="ck_query_logs_stage"
        ),
    )
    op.create_index("ix_query_logs_created_at", "query_logs", ["created_at"])


def downgrade() -> None:
    op.drop_table("query_logs")
    op.drop_table("changelog")
    op.drop_table("chunk_versions")
    op.drop_table("chunks")
