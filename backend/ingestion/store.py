"""Applies a `ChangePlan` to Postgres in one transaction (D-003 versioning)."""

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import delete, select, tuple_, update
from sqlalchemy.orm import Session

from db.models import ChangelogEntry, Chunk, ChunkVersion
from ingestion.diff import ChangePlan, StoredChunk, plan_changes
from ingestion.drafts import ChunkDraft

_ARCHIVED_COLUMNS = (
    "source_type",
    "source_id",
    "chunk_index",
    "title",
    "url",
    "text",
    "lang",
    "authority",
    "nationality_scope",
    "prefecture",
    "journey_stages",
    "procedure_tags",
    "in_force_from",
    "in_force_to",
    "source_updated_at",
    "fetched_at",
    "content_hash",
)


def stored_chunks(session: Session, source_types: Iterable[str]) -> list[StoredChunk]:
    rows = session.execute(
        select(Chunk.id, Chunk.source_type, Chunk.source_id, Chunk.content_hash).where(
            Chunk.source_type.in_(list(source_types))
        )
    ).all()
    return [StoredChunk(r.id, r.source_type, r.source_id, r.content_hash) for r in rows]


def sync(
    session: Session,
    drafts: list[ChunkDraft],
    complete_sources: Iterable[tuple[str, str]],
    fetched_at: datetime,
    complete_source_types: Iterable[str] = (),
) -> ChangePlan:
    """Plans and applies the changes for the source types present in `drafts`,
    `complete_sources` and `complete_source_types`. For a complete source type, every stored
    source of that type is complete: a source no longer loaded is removed. The caller
    commits."""
    complete = set(complete_sources)
    full_types = set(complete_source_types)
    source_types = {d.source_type for d in drafts} | {st for st, _ in complete} | full_types
    stored = stored_chunks(session, source_types)
    complete |= {(s.source_type, s.source_id) for s in stored if s.source_type in full_types}
    plan = plan_changes(stored, drafts, complete)
    apply_plan(session, plan, fetched_at)
    return plan


def apply_plan(session: Session, plan: ChangePlan, fetched_at: datetime) -> None:
    _archive(session, [d.id for d in plan.updates] + [c.id for c in plan.deletes])
    for draft in plan.inserts:
        session.add(Chunk(id=draft.id, **_values(draft, fetched_at)))
    for draft in plan.updates:
        values = {**_values(draft, fetched_at), "embedding": None, "updated_at": fetched_at}
        session.execute(update(Chunk).where(Chunk.id == draft.id).values(**values))
    for draft in plan.refreshes:
        values = {k: v for k, v in _values(draft, fetched_at).items() if k != "content_hash"}
        session.execute(update(Chunk).where(Chunk.id == draft.id).values(**values))
    if plan.deletes:
        session.execute(delete(Chunk).where(Chunk.id.in_([c.id for c in plan.deletes])))
    session.add_all(
        ChangelogEntry(
            source_type=c.source_type,
            source_id=c.source_id,
            chunk_id=c.chunk_id,
            change_type=c.change_type,
            old_hash=c.old_hash,
            new_hash=c.new_hash,
            detected_at=fetched_at,
        )
        for c in plan.changes
    )
    session.flush()


def _archive(session: Session, chunk_ids: list[str]) -> None:
    """Copies the current rows into `chunk_versions` (without embeddings). A row whose
    (chunk_id, content_hash) is already archived is skipped."""
    if not chunk_ids:
        return
    columns = [getattr(Chunk, c) for c in _ARCHIVED_COLUMNS]
    rows = session.execute(select(Chunk.id, *columns).where(Chunk.id.in_(chunk_ids))).all()
    keys = [(r.id, r.content_hash) for r in rows]
    archived: set[tuple[str, str]] = set()
    if keys:
        existing = session.execute(
            select(ChunkVersion.chunk_id, ChunkVersion.content_hash).where(
                tuple_(ChunkVersion.chunk_id, ChunkVersion.content_hash).in_(keys)
            )
        ).all()
        archived = {(str(r[0]), str(r[1])) for r in existing}
    session.add_all(
        ChunkVersion(chunk_id=r.id, **{c: getattr(r, c) for c in _ARCHIVED_COLUMNS})
        for r in rows
        if (r.id, r.content_hash) not in archived
    )


def _values(draft: ChunkDraft, fetched_at: datetime) -> dict[str, Any]:
    return {
        "source_type": draft.source_type,
        "source_id": draft.source_id,
        "chunk_index": draft.chunk_index,
        "title": draft.title,
        "url": draft.url,
        "text": draft.text,
        "lang": draft.lang,
        "authority": draft.meta.authority,
        "nationality_scope": draft.meta.nationality_scope,
        "prefecture": draft.meta.prefecture,
        "journey_stages": list(draft.meta.journey_stages),
        "procedure_tags": list(draft.meta.procedure_tags),
        "in_force_from": draft.in_force_from,
        "in_force_to": draft.in_force_to,
        "source_updated_at": draft.source_updated_at,
        "fetched_at": fetched_at,
        "content_hash": draft.content_hash,
    }
