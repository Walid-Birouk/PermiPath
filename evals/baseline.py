"""Baseline retriever over the pre-vectorised Service-Public.fr dataset (build step 3).

Dataset: DINUM, "Fiches pratiques Service-Public.fr vectorisées", Hugging Face
`AgentPublic/service-public` (config `latest`), Licence Ouverte / Etalab 2.0; texts
Service-Public.gouv.fr / DILA. Chunks of up to 1024 bge-m3 tokens; column `embeddings_bge-m3`
holds the BAAI/bge-m3 vector (1024 dimensions) of `chunk_text`, as a JSON string (dataset card,
read 2026-11-27).

Scope: the fact sheets selected in `sources/manifest.yaml`, so that the baseline and the
pipeline answer from the same documents (maintainer decision, 2026-11-27). The baseline holds
fact sheets only: it cannot retrieve CESEDA articles or the Franco-Algerian agreement.

Rows are fetched per sheet (`doc_id`) through the Hugging Face dataset viewer `filter` endpoint
and saved as a snapshot with the dataset revision:

    uv run --project backend python -m evals.baseline fetch

Questions are embedded locally with BAAI/bge-m3 (sentence-transformers, CPU; optional
dependency group `baseline`). The dataset card does not document query embedding; every run
re-embeds a few stored chunk texts and records the minimum cosine with the stored vectors
(`vector_check`), which must be close to 1 for the comparison to hold.
"""

import argparse
import json
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import httpx2

from evals import REPO_ROOT
from evals.metrics import Hit
from evals.retrievers import Retrieval

DATASET = "AgentPublic/service-public"
DATASET_CONFIG, DATASET_SPLIT = "latest", "train"
DATASET_API_URL = f"https://huggingface.co/api/datasets/{DATASET}"
FILTER_URL = "https://datasets-server.huggingface.co/filter"
"""Hugging Face dataset viewer API (`/filter`): rows matching a SQL `where`, 100 per page at
most; response `rows[].row`, `rows[].truncated_cells`, `num_rows_total`, `partial`."""
EMBEDDING_COLUMN = "embeddings_bge-m3"
EMBEDDING_DIM = 1024
PAGE_SIZE = 20

QUERY_MODEL = "BAAI/bge-m3"
QUERY_MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
"""Pinned model revision (Hugging Face, last modified 2024-07-03)."""

SNAPSHOT_PATH = REPO_ROOT / "reports" / "cache" / "service-public-baseline.json"
CHECKED_CHUNKS = 5


class BaselineError(Exception):
    pass


@dataclass(frozen=True)
class BaselineChunk:
    chunk_id: str
    doc_id: str
    chunk_text: str
    vector: list[float]


@dataclass(frozen=True)
class Snapshot:
    revision: str
    fetched_at: str
    sheets: list[str]
    missing_sheets: list[str]
    chunks: list[BaselineChunk]


# --- fetching -------------------------------------------------------------------------------


def dataset_revision(http: httpx2.Client) -> str:
    response = http.get(DATASET_API_URL)
    response.raise_for_status()
    return str(response.json()["sha"])


def parse_row(row: dict[str, Any]) -> BaselineChunk:
    try:
        vector = [float(x) for x in json.loads(row[EMBEDDING_COLUMN])]
        chunk = BaselineChunk(
            str(row["chunk_id"]), str(row["doc_id"]), str(row["chunk_text"]), vector
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise BaselineError(f"unexpected dataset row: {exc!r}") from exc
    if len(vector) != EMBEDDING_DIM:
        raise BaselineError(f"{chunk.chunk_id}: {len(vector)}-dimensional embedding")
    return chunk


def fetch_sheet(
    http: httpx2.Client, doc_id: str, page_size: int = PAGE_SIZE
) -> list[BaselineChunk]:
    """Every dataset row of one sheet, in dataset order."""
    if not doc_id.isalnum():
        raise BaselineError(f"invalid sheet id: {doc_id!r}")
    chunks: list[BaselineChunk] = []
    offset = 0
    while True:
        params = {
            "dataset": DATASET,
            "config": DATASET_CONFIG,
            "split": DATASET_SPLIT,
            "where": f"\"doc_id\"='{doc_id}'",
            "offset": str(offset),
            "length": str(page_size),
        }
        response = http.get(FILTER_URL, params=params)
        response.raise_for_status()
        payload = response.json()
        if payload.get("partial"):
            raise BaselineError("the dataset viewer index is partial; rows may be missing")
        for item in payload["rows"]:
            if item.get("truncated_cells"):
                raise BaselineError(f"{doc_id}: truncated cells {item['truncated_cells']}")
            chunks.append(parse_row(item["row"]))
        offset += len(payload["rows"])
        if not payload["rows"] or offset >= int(payload["num_rows_total"]):
            return chunks


def build_snapshot(http: httpx2.Client, sheet_ids: Sequence[str], now: datetime) -> Snapshot:
    revision = dataset_revision(http)
    chunks: list[BaselineChunk] = []
    missing: list[str] = []
    for doc_id in sheet_ids:
        rows = fetch_sheet(http, doc_id)
        if not rows:
            missing.append(doc_id)
        chunks.extend(rows)
    return Snapshot(
        revision=revision,
        fetched_at=now.isoformat(timespec="seconds"),
        sheets=list(sheet_ids),
        missing_sheets=missing,
        chunks=chunks,
    )


def save_snapshot(snapshot: Snapshot, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "dataset": DATASET,
        "config": DATASET_CONFIG,
        "revision": snapshot.revision,
        "fetched_at": snapshot.fetched_at,
        "sheets": snapshot.sheets,
        "missing_sheets": snapshot.missing_sheets,
        "chunks": [vars(c) for c in snapshot.chunks],
    }
    path.write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")


def load_snapshot(path: Path) -> Snapshot:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Snapshot(
        revision=data["revision"],
        fetched_at=data["fetched_at"],
        sheets=data["sheets"],
        missing_sheets=data["missing_sheets"],
        chunks=[BaselineChunk(**c) for c in data["chunks"]],
    )


# --- retrieval ------------------------------------------------------------------------------


class QueryEncoder(Protocol):
    @property
    def model(self) -> str: ...

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


class SentenceTransformerEncoder:
    """BAAI/bge-m3 dense embeddings on CPU (dependency group `baseline`)."""

    model = f"{QUERY_MODEL}@{QUERY_MODEL_REVISION[:12]}"

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(QUERY_MODEL, revision=QUERY_MODEL_REVISION, device="cpu")

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        vectors = self._model.encode(list(texts), normalize_embeddings=True)
        return [[float(x) for x in v] for v in vectors]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


def vector_check(chunks: Sequence[BaselineChunk], encoder: QueryEncoder) -> float | None:
    """Minimum cosine between stored vectors and the local re-embedding of their texts."""
    sample = list(chunks[:CHECKED_CHUNKS])
    if not sample:
        return None
    vectors = encoder.encode([c.chunk_text for c in sample])
    return min(cosine(c.vector, v) for c, v in zip(sample, vectors, strict=True))


@dataclass
class BaselineRetriever:
    snapshot: Snapshot
    encoder: QueryEncoder
    name: str = field(default="baseline-sp", init=False)

    def describe(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "embedding_model": f"{self.encoder.model} (local)",
            "dataset": f"{DATASET} ({DATASET_CONFIG})",
            "dataset_revision": self.snapshot.revision,
            "fetched_at": self.snapshot.fetched_at,
            "sheets": len(self.snapshot.sheets) - len(self.snapshot.missing_sheets),
            "missing_sheets": self.snapshot.missing_sheets,
            "chunks": len(self.snapshot.chunks),
            "chunks_by_source_type": {"fact_sheet": len(self.snapshot.chunks)},
            "vector_check": vector_check(self.snapshot.chunks, self.encoder),
        }

    def retrieve(self, question: str, depth: int) -> Retrieval:
        query = self.encoder.encode([question])[0]
        scored = sorted(
            ((cosine(query, c.vector), c) for c in self.snapshot.chunks),
            key=lambda pair: (-pair[0], pair[1].chunk_id),
        )
        hits = [Hit(c.chunk_id, f"fact_sheet:{c.doc_id}", score) for score, c in scored[:depth]]
        return Retrieval(hits=hits, embedding_tokens=0)


# --- CLI ------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals.baseline")
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch", help="snapshot the dataset rows of the manifest sheets")
    fetch.add_argument("--out", type=Path, default=SNAPSHOT_PATH)
    args = parser.parse_args(argv)

    from ingestion.manifest import load_manifest

    sheet_ids = [sheet.id for sheet in load_manifest().fact_sheets.sheets]
    try:
        with httpx2.Client(timeout=60.0) as http:
            snapshot = build_snapshot(http, sheet_ids, datetime.now(UTC))
    except (httpx2.HTTPError, BaselineError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    save_snapshot(snapshot, args.out)
    print(
        f"{DATASET}@{snapshot.revision[:12]}: {len(snapshot.chunks)} chunks from "
        f"{len(sheet_ids) - len(snapshot.missing_sheets)}/{len(sheet_ids)} sheets -> {args.out}"
    )
    if snapshot.missing_sheets:
        print(f"Missing from the dataset: {', '.join(snapshot.missing_sheets)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
