"""ORM models. The Alembic migrations in `db/migrations/versions/` are the source of truth
for the schema; these models mirror them for application code."""

from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    BigInteger,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

EMBEDDING_DIM = 1024
"""Output dimension of `mistral-embed`."""

SOURCE_TYPES = ("law", "treaty", "fact_sheet", "prefecture")
AUTHORITIES = ("official", "secondary")
NATIONALITY_SCOPES = ("all", "algerian")
JOURNEY_STAGES = ("arriving", "renewing", "working", "finishing", "job_offer", "settling")
LANGS = ("fr", "en")
CHANGE_TYPES = ("added", "modified", "removed")
REVIEW_STATUSES = ("pending", "reviewed", "rejected")

TSV_EXPRESSION = (
    "to_tsvector(CASE lang WHEN 'en' THEN 'english'::regconfig ELSE 'french'::regconfig END, "
    "coalesce(title, '') || ' ' || text)"
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _subset(column: str, values: tuple[str, ...]) -> str:
    return f"{column} <@ ARRAY[{', '.join(repr(v) for v in values)}]::text[]"


class Base(DeclarativeBase):
    pass


class Chunk(Base):
    """Current version of a retrievable passage from an official source."""

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("source_type", "source_id", "chunk_index", name="uq_chunks_source_pos"),
        CheckConstraint(_in("source_type", SOURCE_TYPES), name="ck_chunks_source_type"),
        CheckConstraint(_in("lang", LANGS), name="ck_chunks_lang"),
        CheckConstraint(_subset("journey_stages", JOURNEY_STAGES), name="ck_chunks_stages"),
        CheckConstraint(_in("authority", AUTHORITIES), name="ck_chunks_authority"),
        CheckConstraint(
            _in("nationality_scope", NATIONALITY_SCOPES), name="ck_chunks_nationality_scope"
        ),
        Index("ix_chunks_filters", "nationality_scope", "source_type"),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    source_type: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(Text)
    authority: Mapped[str] = mapped_column(Text, server_default="official")
    nationality_scope: Mapped[str] = mapped_column(Text, server_default="all")
    prefecture: Mapped[str | None] = mapped_column(Text)
    journey_stages: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    procedure_tags: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    in_force_from: Mapped[date | None] = mapped_column(Date)
    in_force_to: Mapped[date | None] = mapped_column(Date)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed(TSV_EXPRESSION, persisted=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ChunkVersion(Base):
    """Snapshot of a superseded chunk, kept for the changelog diff. No embedding is stored."""

    __tablename__ = "chunk_versions"
    __table_args__ = (UniqueConstraint("chunk_id", "content_hash", name="uq_chunk_versions"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chunk_id: Mapped[str] = mapped_column(Text, index=True)
    source_type: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(Text)
    chunk_index: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text)
    lang: Mapped[str] = mapped_column(Text)
    authority: Mapped[str] = mapped_column(Text, server_default="official")
    nationality_scope: Mapped[str] = mapped_column(Text, server_default="all")
    prefecture: Mapped[str | None] = mapped_column(Text)
    journey_stages: Mapped[list[str]] = mapped_column(ARRAY(Text))
    procedure_tags: Mapped[list[str]] = mapped_column(ARRAY(Text))
    in_force_from: Mapped[date | None] = mapped_column(Date)
    in_force_to: Mapped[date | None] = mapped_column(Date)
    source_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(Text)
    superseded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ChangelogEntry(Base):
    """A detected change in an official source, published only once reviewed."""

    __tablename__ = "changelog"
    __table_args__ = (
        CheckConstraint(_in("source_type", SOURCE_TYPES), name="ck_changelog_source_type"),
        CheckConstraint(_in("change_type", CHANGE_TYPES), name="ck_changelog_change_type"),
        CheckConstraint(_in("status", REVIEW_STATUSES), name="ck_changelog_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_type: Mapped[str] = mapped_column(Text)
    source_id: Mapped[str] = mapped_column(Text)
    chunk_id: Mapped[str | None] = mapped_column(Text)
    change_type: Mapped[str] = mapped_column(Text)
    old_hash: Mapped[str | None] = mapped_column(Text)
    new_hash: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="pending")
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class QueryLog(Base):
    """Per-request operational log. By design it holds no question or answer text."""

    __tablename__ = "query_logs"
    __table_args__ = (
        CheckConstraint(
            f"journey_stage IS NULL OR {_in('journey_stage', JOURNEY_STAGES)}",
            name="ck_query_logs_stage",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    journey_stage: Mapped[str | None] = mapped_column(Text)
    retrieved_chunk_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    eval_flags: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
