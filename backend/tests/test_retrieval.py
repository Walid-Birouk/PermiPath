"""Hybrid retrieval against Postgres (TEST_DATABASE_URL). Texts are made up for the test."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from db.models import EMBEDDING_DIM, Chunk
from rag.embeddings import EmbeddingBatch
from rag.retrieval import Filters, HybridRetriever

pytestmark = pytest.mark.db


def _unit(axis: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[axis] = 1.0
    return vector


@dataclass
class AxisEmbedder:
    """Embeds every question as the unit vector of `axis`."""

    axis: int
    model: str = "fake-embed"

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        return EmbeddingBatch([_unit(self.axis) for _ in texts], prompt_tokens=7)


def _chunk(chunk_id: str, text: str, axis: int, **meta: object) -> Chunk:
    return Chunk(
        id=f"fact_sheet:{chunk_id}:0",
        source_type=meta.pop("source_type", "fact_sheet"),
        source_id=chunk_id,
        chunk_index=0,
        title=f"Fiche {chunk_id}",
        url=f"https://example.test/{chunk_id}",
        text=text,
        lang="fr",
        journey_stages=["arriving"],
        fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
        content_hash=chunk_id,
        embedding=_unit(axis),
        **meta,
    )


@pytest.fixture
def engine(migrated_engine: Engine) -> Engine:
    with Session(migrated_engine) as session, session.begin():
        session.add_all(
            [
                _chunk("A", "Le passeport du voyageur imaginaire est vert.", axis=0),
                _chunk("B", "La licorne administrative remplit un formulaire bleu.", axis=1),
                _chunk(
                    "ALG",
                    "Le voyageur imaginaire algérien remplit un formulaire jaune.",
                    axis=1,
                    nationality_scope="algerian",
                ),
                _chunk(
                    "OLD",
                    "Ancienne règle du voyageur imaginaire.",
                    axis=0,
                    in_force_to=date(2020, 1, 1),
                ),
                _chunk("PREF", "Liste du voyageur imaginaire.", axis=0, prefecture="Nowhere"),
            ]
        )
    return migrated_engine


def test_rankings_are_fused_and_ids_resolved(engine: Engine) -> None:
    retriever = HybridRetriever(engine, AxisEmbedder(axis=1))
    result = retriever.retrieve(
        "formulaire licorne", Filters(in_force_on=date(2026, 6, 1)), depth=5
    )
    refs = [c.source_ref for c in result.chunks]
    # B matches both rankings; A only the vector ranking (orthogonal, lower similarity).
    assert refs[0] == "fact_sheet:B"
    top = result.chunks[0]
    assert top.lexical_rank == 1 and top.vector_rank == 1
    assert top.url == "https://example.test/B" and "licorne" in top.text
    assert result.embedding_tokens == 7


def test_lexical_query_matches_any_term(engine: Engine) -> None:
    retriever = HybridRetriever(engine, AxisEmbedder(axis=0), weights={"lexical": 1.0, "vector": 0})
    result = retriever.retrieve("passeport inexistantmot", Filters(), depth=5)
    assert [c.source_ref for c in result.chunks] == ["fact_sheet:A"]


def test_algerian_sources_need_the_algerian_scope(engine: Engine) -> None:
    retriever = HybridRetriever(engine, AxisEmbedder(axis=1))
    default = {c.source_id for c in retriever.retrieve("formulaire", Filters(), 10).chunks}
    algerian = {
        c.source_id
        for c in retriever.retrieve(
            "formulaire", Filters(nationality_scopes=("all", "algerian")), 10
        ).chunks
    }
    assert "ALG" not in default
    assert "ALG" in algerian


def test_repealed_and_other_prefecture_chunks_are_filtered(engine: Engine) -> None:
    retriever = HybridRetriever(engine, AxisEmbedder(axis=0))
    on_date = {
        c.source_id
        for c in retriever.retrieve("voyageur", Filters(in_force_on=date(2026, 6, 1)), 10).chunks
    }
    assert "OLD" not in on_date and "PREF" not in on_date
    with_prefecture = {
        c.source_id
        for c in retriever.retrieve("voyageur", Filters(prefecture="Nowhere"), 10).chunks
    }
    assert "PREF" in with_prefecture
    assert "OLD" in with_prefecture  # no date filter
