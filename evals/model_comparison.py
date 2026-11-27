"""Model comparison: the index's own chunks embedded with BAAI/bge-m3 instead of mistral-embed.

Pipeline `bge-m3-index` (D-014): same chunks, same input text (`title + blank line + text`, as
`ingestion.embed`), same ranking (cosine, top `depth` chunks) as `vector`; only the embedding
model differs. Passages and questions are embedded locally on CPU (dependency group
`baseline`). Vectors are cached in `reports/cache/bge-m3-index.json` by chunk id and
`content_hash`, never written to the database; a changed chunk is re-embedded.
"""

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from db.models import Chunk
from evals import REPO_ROOT
from evals.baseline import QueryEncoder, cosine
from evals.metrics import Hit
from evals.retrievers import Retrieval
from ingestion.embed import embedding_input

CACHE_PATH = REPO_ROOT / "reports" / "cache" / "bge-m3-index.json"
ENCODE_BATCH = 16


@dataclass(frozen=True)
class IndexedChunk:
    chunk_id: str
    source_ref: str
    vector: list[float]


def load_cache(path: Path, model: str) -> dict[str, dict[str, Any]]:
    """`{chunk_id: {"hash": ..., "vector": [...]}}`; empty if absent or made by another model."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["vectors"] if data.get("model") == model else {}


def save_cache(path: Path, model: str, vectors: dict[str, dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"model": model, "vectors": vectors}) + "\n", encoding="utf-8")


def embed_index(
    rows: Sequence[tuple[str, str, str, str, str]],
    encoder: QueryEncoder,
    cache: dict[str, dict[str, Any]],
) -> tuple[list[IndexedChunk], int]:
    """rows: (chunk_id, source_ref, content_hash, title, text). Returns the indexed chunks and
    the number of chunks embedded in this call (cache misses). Updates `cache` in place and
    drops entries of chunks no longer in `rows`."""
    stale = [r for r in rows if cache.get(r[0], {}).get("hash") != r[2]]
    for start in range(0, len(stale), ENCODE_BATCH):
        batch = stale[start : start + ENCODE_BATCH]
        vectors = encoder.encode([embedding_input(r[3], r[4]) for r in batch])
        for row, vector in zip(batch, vectors, strict=True):
            cache[row[0]] = {"hash": row[2], "vector": vector}
    for chunk_id in set(cache) - {r[0] for r in rows}:
        del cache[chunk_id]
    chunks = [IndexedChunk(r[0], r[1], cache[r[0]]["vector"]) for r in rows]
    return chunks, len(stale)


@dataclass
class IndexModelRetriever:
    """Cosine ranking over the index's chunks with a local encoder."""

    chunks: list[IndexedChunk]
    encoder: QueryEncoder
    embedded_now: int = 0
    name: str = field(default="bge-m3-index", init=False)

    @classmethod
    def from_database(
        cls, engine: Engine, encoder: QueryEncoder, cache_path: Path = CACHE_PATH
    ) -> "IndexModelRetriever":
        with Session(engine) as session:
            result = session.execute(
                select(
                    Chunk.id,
                    Chunk.source_type,
                    Chunk.source_id,
                    Chunk.content_hash,
                    Chunk.title,
                    Chunk.text,
                ).order_by(Chunk.id)
            )
            rows = [
                (r.id, f"{r.source_type}:{r.source_id}", r.content_hash, r.title, r.text)
                for r in result
            ]
        cache = load_cache(cache_path, encoder.model)
        chunks, embedded = embed_index(rows, encoder, cache)
        save_cache(cache_path, encoder.model, cache)
        return cls(chunks, encoder, embedded)

    def describe(self) -> dict[str, Any]:
        per_type: dict[str, int] = {}
        for chunk in self.chunks:
            source_type = chunk.source_ref.split(":", 1)[0]
            per_type[source_type] = per_type.get(source_type, 0) + 1
        return {
            "name": self.name,
            "embedding_model": f"{self.encoder.model} (local)",
            "chunks": len(self.chunks),
            "chunks_by_source_type": dict(sorted(per_type.items())),
            "chunks_embedded_this_run": self.embedded_now,
        }

    def retrieve(self, question: str, depth: int) -> Retrieval:
        query = self.encoder.encode([question])[0]
        scored = sorted(
            ((cosine(query, c.vector), c) for c in self.chunks),
            key=lambda pair: (-pair[0], pair[1].chunk_id),
        )
        hits = [Hit(c.chunk_id, c.source_ref, score) for score, c in scored[:depth]]
        return Retrieval(hits=hits, embedding_tokens=0)
