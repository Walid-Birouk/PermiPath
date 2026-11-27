"""Retrievers under evaluation. Each one returns ranked chunk hits for a question.

- `vector`: pgvector cosine similarity over the embedded `chunks` (query embedded with the same
  model as the passages, `rag.embeddings`). This is the index built in build step 2, without the
  lexical search, fusion, filters or re-ranking planned for build step 4.
- `hybrid`: full-text and vector rankings fused with RRF, with the metadata filters of the
  app (`rag.retrieval`, build step 4).
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from db.models import Chunk
from evals.metrics import Hit
from rag.embeddings import Embedder


@dataclass(frozen=True)
class Retrieval:
    hits: list[Hit]
    embedding_tokens: int = 0
    """Tokens billed to embed the question (0 when the retriever needs no API call)."""


class Retriever(Protocol):
    name: str

    def describe(self) -> dict[str, Any]:
        """Configuration and corpus facts recorded in the report."""
        ...

    def retrieve(self, question: str, depth: int) -> Retrieval: ...


@dataclass
class VectorRetriever:
    engine: Engine
    embedder: Embedder
    name: str = field(default="vector", init=False)

    def describe(self) -> dict[str, Any]:
        with Session(self.engine) as session:
            rows = session.execute(
                select(Chunk.source_type, func.count())
                .where(Chunk.embedding.is_not(None))
                .group_by(Chunk.source_type)
            ).all()
        per_type = Counter({source_type: count for source_type, count in rows})
        return {
            "name": self.name,
            "embedding_model": self.embedder.model,
            "chunks": sum(per_type.values()),
            "chunks_by_source_type": dict(sorted(per_type.items())),
        }

    def retrieve(self, question: str, depth: int) -> Retrieval:
        batch = self.embedder.embed([question])
        distance = Chunk.embedding.cosine_distance(batch.vectors[0])
        query = (
            select(Chunk.id, Chunk.source_type, Chunk.source_id, distance.label("distance"))
            .where(Chunk.embedding.is_not(None))
            .order_by(distance, Chunk.id)
            .limit(depth)
        )
        with Session(self.engine) as session:
            rows = session.execute(query).all()
        hits = [Hit(r.id, f"{r.source_type}:{r.source_id}", 1.0 - float(r.distance)) for r in rows]
        return Retrieval(hits=hits, embedding_tokens=batch.prompt_tokens)


@dataclass
class HybridEvalRetriever:
    """`rag.retrieval.HybridRetriever` with the filters the app derives from the question:
    nationality detected from the text (`guardrails.nationality`), in force on `on`. With
    `precedence`, retrieves twice the depth, orders it by `rag.precedence` and keeps `depth`."""

    retriever: Any
    on: date
    precedence: bool = False
    name: str = field(init=False)

    def __post_init__(self) -> None:
        self.name = "hybrid-precedence" if self.precedence else "hybrid"

    def describe(self) -> dict[str, Any]:
        r = self.retriever
        return {
            "name": self.name,
            "embedding_model": r.embedder.model,
            "fusion": {"method": "rrf", "k": r.k, "weights": r.weights},
            "candidates_per_ranking": r.candidates,
            "lexical_config": "french",
            "filters": {"nationality": "detected from the question", "in_force_on": str(self.on)},
            "precedence": self.precedence,
        }

    def retrieve(self, question: str, depth: int) -> Retrieval:
        from guardrails.nationality import detect_nationality
        from rag.retrieval import Filters
        from rag.search import search

        algerian = detect_nationality(question) == "algerian"
        if self.precedence:
            found = search(self.retriever, question, algerian, self.on, depth)
            chunks, tokens = found.chunks, found.embedding_tokens
        else:
            scopes = ("all", "algerian") if algerian else ("all",)
            result = self.retriever.retrieve(
                question, Filters(nationality_scopes=scopes, in_force_on=self.on), depth=depth
            )
            chunks, tokens = result.chunks, result.embedding_tokens
        hits = [Hit(c.id, c.source_ref, c.score) for c in chunks]
        return Retrieval(hits=hits, embedding_tokens=tokens)
