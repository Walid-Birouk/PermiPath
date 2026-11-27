"""Embedding of stored chunks: fills `chunks.embedding` where it is NULL (new chunks, and chunks
whose content changed, since `ingestion.store` resets the embedding on modification).

Input text = title + blank line + text, the same fields as the lexical index (`tsv`) and the
`content_hash`, so an embedding is reset exactly when its input changes.

Each batch is committed on its own: an interrupted run keeps the embeddings already written and
the next run continues with the remaining chunks. A vector is written only if the chunk's
`content_hash` is unchanged since it was read.
"""

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass

from sqlalchemy import Engine, bindparam, select, update
from sqlalchemy.orm import Session

from db.models import Chunk
from rag.embeddings import Embedder

MAX_BATCH_INPUTS = 32
MAX_BATCH_CHARS = 40_000
"""Request size caps. The provider documents no per-request limit (see `rag.embeddings`); these
keep each request well below the model's 8K-token context per input and small overall."""


@dataclass(frozen=True)
class PendingChunk:
    id: str
    content_hash: str
    input: str


@dataclass
class EmbedReport:
    chunks: int = 0
    requests: int = 0
    prompt_tokens: int = 0
    skipped: int = 0
    """Chunks whose content changed between read and write (left NULL for the next run)."""


def embedding_input(title: str, text: str) -> str:
    return f"{title}\n\n{text}"


def pending_chunks(session: Session, limit: int | None = None) -> list[PendingChunk]:
    query = (
        select(Chunk.id, Chunk.content_hash, Chunk.title, Chunk.text)
        .where(Chunk.embedding.is_(None))
        .order_by(Chunk.id)
        .limit(limit)
    )
    return [
        PendingChunk(r.id, r.content_hash, embedding_input(r.title, r.text))
        for r in session.execute(query)
    ]


def batches(
    chunks: Sequence[PendingChunk],
    max_inputs: int = MAX_BATCH_INPUTS,
    max_chars: int = MAX_BATCH_CHARS,
) -> Iterator[list[PendingChunk]]:
    """Consecutive groups of at most `max_inputs` chunks and `max_chars` input characters. A
    single chunk longer than `max_chars` forms its own batch."""
    batch: list[PendingChunk] = []
    size = 0
    for chunk in chunks:
        if batch and (len(batch) == max_inputs or size + len(chunk.input) > max_chars):
            yield batch
            batch, size = [], 0
        batch.append(chunk)
        size += len(chunk.input)
    if batch:
        yield batch


_SET_EMBEDDING = (
    update(Chunk)
    .where(Chunk.id == bindparam("chunk_id"), Chunk.content_hash == bindparam("expected_hash"))
    .values(embedding=bindparam("vector"))
)
"""Executed on the connection (Core executemany), not through the ORM session."""


def embed_pending(
    engine: Engine,
    embedder: Embedder,
    limit: int | None = None,
    on_batch: Callable[[EmbedReport], None] | None = None,
) -> EmbedReport:
    """Embeds every chunk without an embedding (at most `limit`), one transaction per batch."""
    report = EmbedReport()
    with Session(engine) as session:
        chunks = pending_chunks(session, limit)
    for batch in batches(chunks):
        result = embedder.embed([c.input for c in batch])
        params = [
            {"chunk_id": c.id, "expected_hash": c.content_hash, "vector": v}
            for c, v in zip(batch, result.vectors, strict=True)
        ]
        with Session(engine) as session, session.begin():
            updated = session.connection().execute(_SET_EMBEDDING, params).rowcount
        report.requests += 1
        report.prompt_tokens += result.prompt_tokens
        report.chunks += updated
        report.skipped += len(batch) - updated
        if on_batch is not None:
            on_batch(report)
    return report
