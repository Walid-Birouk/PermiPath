"""Embedding of stored chunks (`ingestion.embed`): batching, and filling NULL embeddings on
Postgres with a fake embedder (sheet fixture: Service-Public.gouv.fr / DILA, Licence Ouverte 2.0).
Database tests require TEST_DATABASE_URL."""

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session

from db.models import EMBEDDING_DIM, Chunk
from ingestion.drafts import SourceMeta
from ingestion.embed import (
    EmbedReport,
    PendingChunk,
    batches,
    embed_pending,
    embedding_input,
)
from ingestion.service_public.loader import sheet_drafts
from ingestion.service_public.parser import parse_sheet_bytes
from ingestion.store import sync
from rag.embeddings import EmbeddingBatch

SHEET = Path(__file__).parent / "fixtures" / "service_public" / "F2231.xml"
T0 = datetime(2026, 11, 27, tzinfo=UTC)


def _pending(*sizes: int) -> list[PendingChunk]:
    return [PendingChunk(f"c{i}", "h", "x" * n) for i, n in enumerate(sizes)]


def test_embedding_input_is_title_then_text() -> None:
    assert embedding_input("Titre", "Texte") == "Titre\n\nTexte"


def test_batches_respect_the_input_count_cap() -> None:
    groups = list(batches(_pending(*[10] * 5), max_inputs=2, max_chars=1000))
    assert [len(g) for g in groups] == [2, 2, 1]


def test_batches_respect_the_character_cap_and_keep_order() -> None:
    groups = list(batches(_pending(40, 40, 40, 100), max_inputs=10, max_chars=100))
    assert [[c.id for c in g] for g in groups] == [["c0", "c1"], ["c2"], ["c3"]]


def test_an_oversized_chunk_forms_its_own_batch() -> None:
    groups = list(batches(_pending(10, 500, 10), max_inputs=10, max_chars=100))
    assert [[c.id for c in g] for g in groups] == [["c0"], ["c1"], ["c2"]]


def test_no_chunks_no_batches() -> None:
    assert list(batches([])) == []


class FakeEmbedder:
    """Returns one constant fake vector per input; records the inputs of each call."""

    model = "fake-embed"

    def __init__(self, before_return: object = None) -> None:
        self.calls: list[list[str]] = []
        self._before_return = before_return

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        self.calls.append(list(texts))
        if callable(self._before_return):
            self._before_return()
        return EmbeddingBatch(vectors=[[0.5] * EMBEDDING_DIM for _ in texts], prompt_tokens=3)


def _ingest(engine: Engine) -> int:
    drafts = sheet_drafts(parse_sheet_bytes(SHEET.read_bytes()), SourceMeta())
    with Session(engine) as session, session.begin():
        sync(session, drafts, [("fact_sheet", "F2231")], T0)
    return len(drafts)


def _missing(engine: Engine) -> int:
    with Session(engine) as session:
        query = select(func.count()).select_from(Chunk).where(Chunk.embedding.is_(None))
        return session.scalar(query) or 0


@pytest.mark.db
def test_embed_fills_every_missing_embedding_once(migrated_engine: Engine) -> None:
    total = _ingest(migrated_engine)
    embedder = FakeEmbedder()
    progress: list[int] = []
    report = embed_pending(migrated_engine, embedder, on_batch=lambda r: progress.append(r.chunks))
    assert (report.chunks, report.skipped) == (total, 0)
    assert report.requests == len(embedder.calls) == len(progress)
    assert report.prompt_tokens == 3 * report.requests
    assert _missing(migrated_engine) == 0
    with Session(migrated_engine) as session:
        row = session.get(Chunk, "fact_sheet:F2231:0")
        assert row is not None and row.embedding is not None
        assert row.embedding[0] == pytest.approx(0.5)
        assert f"{row.title}\n\n{row.text}" in embedder.calls[0]
    assert embed_pending(migrated_engine, FakeEmbedder()) == EmbedReport()


@pytest.mark.db
def test_limit_embeds_only_the_first_chunks(migrated_engine: Engine) -> None:
    total = _ingest(migrated_engine)
    assert embed_pending(migrated_engine, FakeEmbedder(), limit=2).chunks == 2
    assert _missing(migrated_engine) == total - 2


@pytest.mark.db
def test_chunk_changed_during_the_call_is_left_for_the_next_run(migrated_engine: Engine) -> None:
    total = _ingest(migrated_engine)

    def edit_first_chunk() -> None:
        with Session(migrated_engine) as session, session.begin():
            changed = update(Chunk).where(Chunk.id == "fact_sheet:F2231:0")
            session.execute(changed.values(content_hash="changed"))

    report = embed_pending(migrated_engine, FakeEmbedder(edit_first_chunk), limit=1)
    assert (report.chunks, report.skipped) == (0, 1)
    assert _missing(migrated_engine) == total
