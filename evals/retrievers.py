"""Retrievers under evaluation. Each one returns ranked chunk hits for a question.

- `vector`: pgvector cosine similarity over the embedded `chunks` (query embedded with the same
  model as the passages, `rag.embeddings`). This is the index built in build step 2, without the
  lexical search, fusion, filters or re-ranking planned for build step 4.
"""

from collections import Counter
from dataclasses import dataclass, field
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
