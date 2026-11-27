"""Model x corpus retrieval grid: every corpus embedded with every model, same golden set.

    uv run --project backend --group baseline python -m evals.grid [--set golden]
        [--corpus NAME ...] [--model NAME ...] [--append docs/eval-results.md]

Corpora (passages):

| Name | Content |
|---|---|
| `service-public` | Pre-vectorised service-public dataset chunks (baseline snapshot, 19 sheets). |
| `index-sheets` | The index's fact-sheet chunks for the same sheets as the snapshot. |
| `index` | Every chunk of the index (fact sheets, CESEDA, agreement, case law). |

Models: `mistral-embed` (API, billed) and `bge-m3` (BAAI/bge-m3, local CPU). Each corpus is
embedded the way its producer embeds it: index chunks as `title + text` (`ingestion.embed`),
dataset chunks as `chunk_text`. Passage vectors are cached in `reports/cache/grid-*.json` by
chunk id and content hash; index vectors for `mistral-embed` are read from the database (same
model, same input), so only the dataset chunks are billed once. Queries are embedded with the
cell's model. One report per cell in `reports/`, and a summary table on stdout.
"""

import argparse
import hashlib
import json
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals import REPO_ROOT
from evals.baseline import QueryEncoder, cosine
from evals.golden_set import GoldenSet, load_golden_set, set_path
from evals.metrics import Hit
from evals.retrievers import Retrieval

CORPORA = ("service-public", "index-sheets", "index")
MODELS = ("mistral-embed", "bge-m3")
CACHE_DIR = REPO_ROOT / "reports" / "cache"
SUMMARY_METRICS = (
    ("recall@5", "Source recall@5"),
    ("recall@10", "Source recall@10"),
    ("mrr", "MRR"),
    ("right_source_wins", "Right source wins"),
    ("latency_ms_p50", "Latency p50 (ms)"),
)


@dataclass(frozen=True)
class Passage:
    chunk_id: str
    source_ref: str
    content_hash: str
    input: str
    vector: list[float] | None = None
    """Known vector for the cell's model (index chunks with `mistral-embed`)."""


@dataclass(frozen=True)
class Embedded:
    chunk_id: str
    source_ref: str
    vector: list[float]


@dataclass
class TokenCountingEncoder:
    """`QueryEncoder` over an `Embedder` (API): batches inputs and counts billed tokens."""

    embedder: Any
    max_inputs: int = 32
    max_chars: int = 40_000
    tokens: int = 0

    @property
    def model(self) -> str:
        return str(self.embedder.model)

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        batch: list[str] = []
        for text in texts:
            if batch and (
                len(batch) == self.max_inputs or sum(map(len, batch)) + len(text) > self.max_chars
            ):
                vectors.extend(self._embed(batch))
                batch = []
            batch.append(text)
        if batch:
            vectors.extend(self._embed(batch))
        return vectors

    def _embed(self, texts: list[str]) -> list[list[float]]:
        result = self.embedder.embed(texts)
        self.tokens += result.prompt_tokens
        return [list(v) for v in result.vectors]


def embed_passages(
    passages: Sequence[Passage], encoder: QueryEncoder, cache_path: Path
) -> tuple[list[Embedded], int]:
    """Vectors for every passage: known vector, else cache (same hash and model), else encoded.
    Returns the passages and the number encoded in this call; the cache keeps only `passages`."""
    cache: dict[str, dict[str, Any]] = {}
    if cache_path.exists():
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        cache = data["vectors"] if data.get("model") == encoder.model else {}
    missing = [
        p
        for p in passages
        if p.vector is None and cache.get(p.chunk_id, {}).get("hash") != p.content_hash
    ]
    for start in range(0, len(missing), 16):
        batch = missing[start : start + 16]
        for passage, vector in zip(batch, encoder.encode([p.input for p in batch]), strict=True):
            cache[passage.chunk_id] = {"hash": passage.content_hash, "vector": vector}
    kept = {p.chunk_id: cache[p.chunk_id] for p in passages if p.vector is None}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(
        json.dumps({"model": encoder.model, "vectors": kept}) + "\n", encoding="utf-8"
    )
    embedded = [
        Embedded(
            p.chunk_id,
            p.source_ref,
            p.vector if p.vector is not None else kept[p.chunk_id]["vector"],
        )
        for p in passages
    ]
    return embedded, len(missing)


@dataclass
class GridRetriever:
    corpus: str
    passages: list[Embedded]
    encoder: QueryEncoder
    embedded_now: int = 0
    query_tokens: Callable[[], int] = field(default=lambda: 0)
    name: str = field(init=False)

    def __post_init__(self) -> None:
        self.name = f"grid:{self.corpus}:{self.encoder.model}"

    def describe(self) -> dict[str, Any]:
        per_type: dict[str, int] = {}
        for passage in self.passages:
            source_type = passage.source_ref.split(":", 1)[0]
            per_type[source_type] = per_type.get(source_type, 0) + 1
        return {
            "name": self.name,
            "corpus": self.corpus,
            "embedding_model": self.encoder.model,
            "chunks": len(self.passages),
            "chunks_by_source_type": dict(sorted(per_type.items())),
            "chunks_embedded_this_run": self.embedded_now,
        }

    def retrieve(self, question: str, depth: int) -> Retrieval:
        before = self.query_tokens()
        query = self.encoder.encode([question])[0]
        scored = sorted(
            ((cosine(query, p.vector), p) for p in self.passages),
            key=lambda pair: (-pair[0], pair[1].chunk_id),
        )
        hits = [Hit(p.chunk_id, p.source_ref, score) for score, p in scored[:depth]]
        return Retrieval(hits=hits, embedding_tokens=self.query_tokens() - before)


# --- corpora --------------------------------------------------------------------------------


def snapshot_passages() -> tuple[list[Passage], list[str]]:
    from evals.baseline import SNAPSHOT_PATH, load_snapshot

    snapshot = load_snapshot(SNAPSHOT_PATH)
    passages = [
        Passage(
            c.chunk_id,
            f"fact_sheet:{c.doc_id}",
            hashlib.sha256(c.chunk_text.encode("utf-8")).hexdigest(),
            c.chunk_text,
        )
        for c in snapshot.chunks
    ]
    return passages, sorted(set(snapshot.sheets) - set(snapshot.missing_sheets))


def index_passages(
    engine: Any, with_vectors: bool, sheets: Sequence[str] | None = None
) -> list[Passage]:
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from db.models import Chunk
    from ingestion.embed import embedding_input

    query = select(
        Chunk.id,
        Chunk.source_type,
        Chunk.source_id,
        Chunk.content_hash,
        Chunk.title,
        Chunk.text,
        Chunk.embedding,
    ).order_by(Chunk.id)
    if sheets is not None:
        query = query.where(Chunk.source_type == "fact_sheet", Chunk.source_id.in_(list(sheets)))
    with Session(engine) as session:
        rows = session.execute(query).all()
    passages = []
    for r in rows:
        vector = (
            [float(x) for x in r.embedding] if with_vectors and r.embedding is not None else None
        )
        passages.append(
            Passage(
                r.id,
                f"{r.source_type}:{r.source_id}",
                r.content_hash,
                embedding_input(r.title, r.text),
                vector,
            )
        )
    return passages


# --- CLI ------------------------------------------------------------------------------------


def _encoders(
    models: Sequence[str], stack: ExitStack
) -> dict[str, tuple[QueryEncoder, Callable[[], int]]]:
    encoders: dict[str, tuple[QueryEncoder, Callable[[], int]]] = {}
    if "bge-m3" in models:
        from evals.baseline import SentenceTransformerEncoder

        local = SentenceTransformerEncoder()
        encoders["bge-m3"] = (local, lambda: 0)
    if "mistral-embed" in models:
        from app.config import get_settings
        from rag.embeddings import MistralEmbedder

        key = get_settings().mistral_api_key
        if key is None:
            raise SystemExit("MISTRAL_API_KEY is not set in .env")
        api = TokenCountingEncoder(stack.enter_context(MistralEmbedder(key.get_secret_value())))
        encoders["mistral-embed"] = (api, lambda: api.tokens)
    return encoders


def render_table(reports: dict[tuple[str, str], dict[str, Any]]) -> str:
    cells = sorted(reports, key=lambda k: (CORPORA.index(k[0]), MODELS.index(k[1])))
    header = "| Metric | " + " | ".join(f"{c} x {m}" for c, m in cells) + " |"
    lines = [header, "|---|" + "---|" * len(cells)]
    lines.append(
        "| Chunks | " + " | ".join(str(reports[k]["pipeline"]["chunks"]) for k in cells) + " |"
    )
    for key, label in SUMMARY_METRICS:
        values = []
        for cell in cells:
            value = reports[cell]["summary"].get(key)
            values.append(
                "-" if value is None else f"{value:.3f}" if isinstance(value, float) else str(value)
            )
        lines.append(f"| {label} | " + " | ".join(values) + " |")
    first = next(iter(reports.values()))
    scored = first["summary"].get("items_scored")
    return (
        f"Set `{first['set']['name']}` v{first['set']['version']}, {first['set']['items']} items, "
        f"{scored} scored; created {first['created_at']}.\n\n" + "\n".join(lines) + "\n"
    )


def main(argv: list[str] | None = None) -> int:
    from evals.run_evals import run

    parser = argparse.ArgumentParser(prog="python -m evals.grid")
    parser.add_argument("--set", default="golden")
    parser.add_argument("--corpus", nargs="+", choices=CORPORA, default=list(CORPORA))
    parser.add_argument("--model", nargs="+", choices=MODELS, default=list(MODELS))
    parser.add_argument("--include-drafts", action="store_true")
    parser.add_argument("--append", type=Path, help="append the table to this Markdown file")
    args = parser.parse_args(argv)

    golden: GoldenSet = load_golden_set(set_path(args.set))
    date = datetime.now(UTC).date().isoformat()
    reports: dict[tuple[str, str], dict[str, Any]] = {}
    with ExitStack() as stack:
        from app.config import get_settings
        from db.session import make_engine

        engine = make_engine(get_settings().database_url)
        stack.callback(engine.dispose)
        encoders = _encoders(args.model, stack)
        dataset, sheets = snapshot_passages()
        for corpus in args.corpus:
            for model in args.model:
                encoder, tokens = encoders[model]
                if corpus == "service-public":
                    passages = dataset
                else:
                    passages = index_passages(
                        engine,
                        with_vectors=model == "mistral-embed",
                        sheets=sheets if corpus == "index-sheets" else None,
                    )
                start_tokens = tokens()
                embedded, count = embed_passages(
                    passages, encoder, CACHE_DIR / f"grid-{corpus}-{model}.json"
                )
                passage_tokens = tokens() - start_tokens
                print(
                    f"[{corpus} x {model}] {len(embedded)} passages, {count} embedded now"
                    f"{f', {passage_tokens} tokens' if passage_tokens else ''}",
                    flush=True,
                )
                retriever = GridRetriever(corpus, embedded, encoder, count, tokens)
                report = run(golden, args.set, retriever, include_drafts=args.include_drafts)
                report["pipeline"]["passage_embedding_tokens"] = passage_tokens
                out = REPO_ROOT / "reports" / f"{date}-grid-{corpus}-{model}.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(
                    json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )
                reports[(corpus, model)] = report
    table = render_table(reports)
    print("\n" + table)
    if args.append:
        with args.append.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(f"\n## Model x corpus grid ({date})\n\n{table}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
