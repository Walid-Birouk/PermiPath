"""Storage of chunk drafts with versioning and changelog, on a real sheet (Service-Public.gouv.fr
/ DILA, Licence Ouverte 2.0). Requires Postgres (TEST_DATABASE_URL)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from db.models import ChangelogEntry, Chunk, ChunkVersion
from ingestion.drafts import ChunkDraft, SourceMeta
from ingestion.service_public.loader import sheet_drafts
from ingestion.service_public.parser import parse_sheet_bytes
from ingestion.store import sync

pytestmark = pytest.mark.db

SHEET = Path(__file__).parent / "fixtures" / "service_public" / "F2231.xml"
# A sentence present once in F2231, used to simulate an edit of the official sheet.
ORIGINAL = "Avant de commencer votre démarche, vous devez consulter le site officiel France-visas"
EDITED = "Avant de commencer votre démarche, consultez le site officiel France-visas"
META = SourceMeta(journey_stages=("arriving", "renewing"))
T0, T1, T2 = (datetime(2026, 11, 27, h, tzinfo=UTC) for h in (1, 2, 3))


def _drafts(xml: bytes, meta: SourceMeta = META) -> list[ChunkDraft]:
    return sheet_drafts(parse_sheet_bytes(xml), meta)


def _sync(engine: Engine, drafts: list[ChunkDraft], at: datetime) -> int:
    with Session(engine) as session, session.begin():
        return len(sync(session, drafts, [("fact_sheet", "F2231")], at).changes)


def _count(engine: Engine, model: type[Chunk | ChunkVersion | ChangelogEntry]) -> int:
    with Session(engine) as session:
        return session.scalar(select(func.count()).select_from(model)) or 0


def test_fixture_contains_the_edited_sentence_once() -> None:
    assert SHEET.read_bytes().decode().count(ORIGINAL) == 1


def test_first_ingest_adds_all_chunks(migrated_engine: Engine) -> None:
    drafts = _drafts(SHEET.read_bytes())
    assert _sync(migrated_engine, drafts, T0) == len(drafts)
    assert _count(migrated_engine, Chunk) == len(drafts)
    with Session(migrated_engine) as session:
        row = session.get(Chunk, "fact_sheet:F2231:0")
        assert row is not None
        assert (row.authority, row.nationality_scope) == ("official", "all")
        assert row.journey_stages == ["arriving", "renewing"]


def test_reingest_is_idempotent(migrated_engine: Engine) -> None:
    drafts = _drafts(SHEET.read_bytes())
    _sync(migrated_engine, drafts, T0)
    assert _sync(migrated_engine, drafts, T1) == 0
    assert _count(migrated_engine, ChunkVersion) == 0
    assert _count(migrated_engine, ChangelogEntry) == len(drafts)


def test_modified_sheet_gives_exactly_one_changelog_entry(migrated_engine: Engine) -> None:
    original = SHEET.read_bytes()
    _sync(migrated_engine, _drafts(original), T0)
    edited = _drafts(original.decode().replace(ORIGINAL, EDITED).encode())

    assert _sync(migrated_engine, edited, T1) == 1

    with Session(migrated_engine) as session:
        (entry,) = session.scalars(
            select(ChangelogEntry).where(ChangelogEntry.detected_at == T1)
        ).all()
        assert (entry.change_type, entry.status, entry.source_id) == (
            "modified",
            "pending",
            "F2231",
        )
        (version,) = session.scalars(select(ChunkVersion)).all()
        assert version.chunk_id == entry.chunk_id
        assert ORIGINAL in version.text
        current = session.get(Chunk, entry.chunk_id)
        assert current is not None
        assert EDITED in current.text
        assert current.embedding is None
    assert _sync(migrated_engine, edited, T2) == 0


def test_metadata_change_updates_rows_without_changelog(migrated_engine: Engine) -> None:
    xml = SHEET.read_bytes()
    _sync(migrated_engine, _drafts(xml), T0)
    algerian = SourceMeta(nationality_scope="algerian")
    assert _sync(migrated_engine, _drafts(xml, algerian), T1) == 0
    with Session(migrated_engine) as session:
        scopes = set(session.scalars(select(Chunk.nationality_scope)).all())
    assert scopes == {"algerian"}


def test_removed_chunks_are_archived(migrated_engine: Engine) -> None:
    drafts = _drafts(SHEET.read_bytes())
    _sync(migrated_engine, drafts, T0)
    assert _sync(migrated_engine, drafts[:-1], T1) == 1
    assert _count(migrated_engine, Chunk) == len(drafts) - 1
    assert _count(migrated_engine, ChunkVersion) == 1
