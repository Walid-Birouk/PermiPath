"""Vector retriever (`evals.retrievers.VectorRetriever`) on Postgres with fake vectors (sheet
fixture: Service-Public.gouv.fr / DILA, Licence Ouverte 2.0). Requires TEST_DATABASE_URL."""

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, update
from sqlalchemy.orm import Session

from db.models import EMBEDDING_DIM, Chunk
from evals.retrievers import VectorRetriever
from ingestion.drafts import SourceMeta
from ingestion.service_public.loader import sheet_drafts
from ingestion.service_public.parser import parse_sheet_bytes
from ingestion.store import sync
from rag.embeddings import EmbeddingBatch

pytestmark = pytest.mark.db

SHEET = Path(__file__).parent / "fixtures" / "service_public" / "F2231.xml"
TARGET = "fact_sheet:F2231:1"


def _unit(axis: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[axis] = 1.0
    return vector


class AxisEmbedder:
    model = "fake-embed"

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        return EmbeddingBatch(vectors=[_unit(0) for _ in texts], prompt_tokens=5)


def test_vector_retriever_ranks_by_cosine_similarity(migrated_engine: Engine) -> None:
    drafts = sheet_drafts(parse_sheet_bytes(SHEET.read_bytes()), SourceMeta())
    with Session(migrated_engine) as session, session.begin():
        sync(session, drafts, [("fact_sheet", "F2231")], datetime(2026, 1, 1, tzinfo=UTC))
        session.execute(update(Chunk).values(embedding=_unit(1)))
        session.execute(update(Chunk).where(Chunk.id == TARGET).values(embedding=_unit(0)))
        session.execute(
            update(Chunk).where(Chunk.id == "fact_sheet:F2231:0").values(embedding=None)
        )

    retriever = VectorRetriever(migrated_engine, AxisEmbedder())
    retrieval = retriever.retrieve("question", depth=3)
    assert retrieval.hits[0].chunk_id == TARGET
    assert retrieval.hits[0].score == pytest.approx(1.0)
    assert retrieval.hits[1].score == pytest.approx(0.0)
    assert {h.source_ref for h in retrieval.hits} == {"fact_sheet:F2231"}
    assert retrieval.embedding_tokens == 5
    described = retriever.describe()
    assert described["chunks"] == len(drafts) - 1  # the chunk without embedding is not counted
    assert described["embedding_model"] == "fake-embed"
