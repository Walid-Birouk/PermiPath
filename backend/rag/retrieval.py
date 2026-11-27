"""Hybrid retrieval: Postgres full-text and pgvector rankings fused with RRF (`rag.fusion`).

Both rankings apply the same metadata filters (nationality scope, source types, in force on a
date, prefecture). The lexical query ORs the question's stemmed terms (`plainto_tsquery` would
AND them, which almost never matches a whole question) and ranks with `ts_rank_cd`; the query
is parsed with the `french` configuration, the language of the indexed text. The vector ranking
is the cosine distance to the question embedded with the index's model.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import ColumnElement, Engine, Select, Text, and_, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import REGCONFIG, TSQUERY
from sqlalchemy.orm import Session

from db.models import Chunk
from rag.embeddings import Embedder
from rag.fusion import DEFAULT_K, rrf

CANDIDATES = 50
"""Depth of each ranking before fusion."""
QUERY_CONFIG = "french"
DEFAULT_WEIGHTS = {"lexical": 0.5, "vector": 1.0}
"""The lexical ranking alone is much weaker than the vector ranking on golden v1 (MRR 0.417 vs
0.840) but finds governing sources the vector ranking misses; half weight (D-016)."""


@dataclass(frozen=True)
class Filters:
    nationality_scopes: tuple[str, ...] = ("all",)
    """`("all",)` excludes Algerian-specific sources; `("all", "algerian")` includes them."""
    source_types: tuple[str, ...] | None = None
    in_force_on: date | None = None
    prefecture: str | None = None
    """When set, prefecture-specific chunks of other prefectures are excluded."""


@dataclass(frozen=True)
class RetrievedChunk:
    id: str
    source_type: str
    source_id: str
    title: str
    url: str
    text: str
    nationality_scope: str
    authority: str
    score: float
    lexical_rank: int | None
    vector_rank: int | None
    in_force_from: date | None = None
    source_updated_at: datetime | None = None
    fetched_at: datetime | None = None
    """When the source was last checked (rule 3)."""

    @property
    def source_ref(self) -> str:
        return f"{self.source_type}:{self.source_id}"


@dataclass(frozen=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]
    embedding_tokens: int
    lexical_hits: int
    vector_hits: int
    query_vector: list[float] = field(default_factory=list)


@dataclass
class HybridRetriever:
    engine: Engine
    embedder: Embedder
    candidates: int = CANDIDATES
    k: int = DEFAULT_K
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    rank_normalization: int = 0
    """`ts_rank_cd` normalization bitmask (0: none; 1: divide by 1 + log(length); 32: rank /
    (rank + 1)). PostgreSQL documentation, "Ranking Search Results"."""

    def retrieve(
        self,
        question: str,
        filters: Filters,
        depth: int = 10,
        query_vector: Sequence[float] | None = None,
    ) -> RetrievalResult:
        """`query_vector`: the question already embedded with the index's model (no API call)."""
        if query_vector is None:
            batch = self.embedder.embed([question])
            vector, tokens = list(batch.vectors[0]), batch.prompt_tokens
        else:
            vector, tokens = list(query_vector), 0
        with Session(self.engine) as session:
            lexical = [r.id for r in session.execute(self._lexical(question, filters))]
            by_vector = [r.id for r in session.execute(self._vector(vector, filters))]
            fused = rrf({"lexical": lexical, "vector": by_vector}, k=self.k, weights=self.weights)
            top = fused[:depth]
            rows = {
                r.id: r
                for r in session.execute(
                    select(Chunk).where(Chunk.id.in_([doc_id for doc_id, _ in top]))
                ).scalars()
            }
        lexical_rank = {doc_id: i for i, doc_id in enumerate(lexical, start=1)}
        vector_rank = {doc_id: i for i, doc_id in enumerate(by_vector, start=1)}
        chunks = [
            RetrievedChunk(
                id=doc_id,
                source_type=rows[doc_id].source_type,
                source_id=rows[doc_id].source_id,
                title=rows[doc_id].title,
                url=rows[doc_id].url,
                text=rows[doc_id].text,
                nationality_scope=rows[doc_id].nationality_scope,
                authority=rows[doc_id].authority,
                score=score,
                lexical_rank=lexical_rank.get(doc_id),
                vector_rank=vector_rank.get(doc_id),
                in_force_from=rows[doc_id].in_force_from,
                source_updated_at=rows[doc_id].source_updated_at,
                fetched_at=rows[doc_id].fetched_at,
            )
            for doc_id, score in top
        ]
        return RetrievalResult(chunks, tokens, len(lexical), len(by_vector), vector)

    def _lexical(self, question: str, filters: Filters) -> Select[str]:
        config = cast(literal(QUERY_CONFIG), REGCONFIG)
        terms = cast(func.plainto_tsquery(config, question), Text)
        query = cast(func.to_tsquery(config, func.replace(terms, "&", "|")), TSQUERY)
        rank = func.ts_rank_cd(Chunk.tsv, query, self.rank_normalization)
        return (
            select(Chunk.id)
            .where(Chunk.tsv.op("@@")(query), *_conditions(filters))
            .order_by(rank.desc(), Chunk.id)
            .limit(self.candidates)
        )

    def _vector(self, vector: Sequence[float], filters: Filters) -> Select[str]:
        distance = Chunk.embedding.cosine_distance(list(vector))
        return (
            select(Chunk.id)
            .where(Chunk.embedding.is_not(None), *_conditions(filters))
            .order_by(distance, Chunk.id)
            .limit(self.candidates)
        )


def _conditions(filters: Filters) -> list[ColumnElement[bool]]:
    conditions: list[ColumnElement[bool]] = [
        Chunk.nationality_scope.in_(filters.nationality_scopes)
    ]
    if filters.source_types is not None:
        conditions.append(Chunk.source_type.in_(filters.source_types))
    if filters.in_force_on is not None:
        on = filters.in_force_on
        conditions.append(
            and_(
                or_(Chunk.in_force_from.is_(None), Chunk.in_force_from <= on),
                or_(Chunk.in_force_to.is_(None), Chunk.in_force_to > on),
            )
        )
    if filters.prefecture is not None:
        conditions.append(or_(Chunk.prefecture.is_(None), Chunk.prefecture == filters.prefecture))
    else:
        conditions.append(Chunk.prefecture.is_(None))
    return conditions
