"""Baseline over the pre-vectorised service-public dataset (`evals.baseline`), with a mocked
dataset viewer API and a fake query encoder. Response layout as returned by the Hugging Face
`/filter` endpoint (checked live on 2026-11-27); values are fake."""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest

from evals.baseline import (
    EMBEDDING_COLUMN,
    EMBEDDING_DIM,
    BaselineChunk,
    BaselineError,
    BaselineRetriever,
    Snapshot,
    build_snapshot,
    cosine,
    fetch_sheet,
    load_snapshot,
    save_snapshot,
    vector_check,
)

REVISION = "fake-revision"


def _unit(axis: int) -> list[float]:
    vector = [0.0] * EMBEDDING_DIM
    vector[axis] = 1.0
    return vector


def _row(doc_id: str, index: int, vector: list[float] | None = None) -> dict[str, Any]:
    return {
        "row_idx": index,
        "row": {
            "chunk_id": f"{doc_id}_{index}",
            "doc_id": doc_id,
            "chunk_index": index,
            "chunk_text": f"FAKE TEXT {doc_id} {index}",
            EMBEDDING_COLUMN: json.dumps(vector or _unit(index)),
        },
        "truncated_cells": [],
    }


class FakeViewer:
    """Serves `rows_by_sheet` page by page; records the requested pages."""

    def __init__(self, rows_by_sheet: dict[str, int], partial: bool = False) -> None:
        self.rows_by_sheet = rows_by_sheet
        self.partial = partial
        self.requests: list[tuple[str, int, int]] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "huggingface.co":
            return httpx2.Response(200, json={"sha": REVISION})
        params = request.url.params
        doc_id = params["where"].split("'")[1]
        offset, length = int(params["offset"]), int(params["length"])
        self.requests.append((doc_id, offset, length))
        total = self.rows_by_sheet.get(doc_id, 0)
        rows = [_row(doc_id, i + 1) for i in range(offset, min(offset + length, total))]
        payload = {"rows": rows, "num_rows_total": total, "partial": self.partial}
        return httpx2.Response(200, json=payload)


def _http(handler: Any) -> httpx2.Client:
    return httpx2.Client(transport=httpx2.MockTransport(handler))


def test_fetch_sheet_pages_through_all_rows() -> None:
    viewer = FakeViewer({"F0001": 5})
    chunks = fetch_sheet(_http(viewer), "F0001", page_size=2)
    assert [c.chunk_id for c in chunks] == [f"F0001_{i}" for i in range(1, 6)]
    assert viewer.requests == [("F0001", 0, 2), ("F0001", 2, 2), ("F0001", 4, 2)]
    assert chunks[0].vector == _unit(1)


def test_partial_index_and_truncated_cells_are_errors() -> None:
    with pytest.raises(BaselineError, match="partial"):
        fetch_sheet(_http(FakeViewer({"F0001": 1}, partial=True)), "F0001")

    def truncated(_: httpx2.Request) -> httpx2.Response:
        row = {**_row("F0001", 1), "truncated_cells": [EMBEDDING_COLUMN]}
        return httpx2.Response(200, json={"rows": [row], "num_rows_total": 1, "partial": False})

    with pytest.raises(BaselineError, match="truncated"):
        fetch_sheet(_http(truncated), "F0001")


def test_wrong_dimension_and_unsafe_sheet_id_are_errors() -> None:
    def short(_: httpx2.Request) -> httpx2.Response:
        row = _row("F0001", 1, vector=[0.1, 0.2])
        return httpx2.Response(200, json={"rows": [row], "num_rows_total": 1, "partial": False})

    with pytest.raises(BaselineError, match="2-dimensional"):
        fetch_sheet(_http(short), "F0001")
    with pytest.raises(BaselineError, match="invalid sheet id"):
        fetch_sheet(_http(FakeViewer({})), "F1' OR '1'='1")


def test_snapshot_records_revision_and_missing_sheets(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    snapshot = build_snapshot(_http(FakeViewer({"F0001": 2})), ["F0001", "F0002"], now)
    assert (snapshot.revision, snapshot.missing_sheets) == (REVISION, ["F0002"])
    path = tmp_path / "snapshot.json"
    save_snapshot(snapshot, path)
    assert load_snapshot(path) == snapshot


class AxisEncoder:
    """Embeds any text on axis 1, except texts containing 'two' (axis 2)."""

    model = "fake-encoder"

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        return [_unit(2) if "two" in t else _unit(1) for t in texts]


def _snapshot() -> Snapshot:
    chunks = [
        BaselineChunk("F0001_1", "F0001", "FAKE one", [0.0, 2.0] + [0.0] * (EMBEDDING_DIM - 2)),
        BaselineChunk("F0002_1", "F0002", "FAKE two", _unit(2)),
        BaselineChunk("F0001_2", "F0001", "FAKE three", _unit(3)),
    ]
    return Snapshot(REVISION, "2026-01-01T00:00:00+00:00", ["F0001", "F0002"], [], chunks)


def test_baseline_ranks_by_cosine_and_cites_sheets() -> None:
    retriever = BaselineRetriever(_snapshot(), AxisEncoder())
    retrieval = retriever.retrieve("question two", depth=2)
    assert [h.chunk_id for h in retrieval.hits] == ["F0002_1", "F0001_1"]
    assert retrieval.hits[0].source_ref == "fact_sheet:F0002"
    assert retrieval.hits[0].score == pytest.approx(1.0)
    assert retrieval.embedding_tokens == 0


def test_vector_check_compares_stored_and_local_embeddings() -> None:
    # "FAKE one" is re-embedded on axis 1, as stored (norm 2): cosine 1. "FAKE three" is
    # re-embedded on axis 1 but stored on axis 3: cosine 0, the minimum.
    assert vector_check(_snapshot().chunks, AxisEncoder()) == pytest.approx(0.0)
    assert vector_check(_snapshot().chunks[:2], AxisEncoder()) == pytest.approx(1.0)
    assert vector_check([], AxisEncoder()) is None
    described = BaselineRetriever(_snapshot(), AxisEncoder()).describe()
    assert described["dataset_revision"] == REVISION
    assert described["chunks"] == 3


def test_cosine_handles_zero_vectors() -> None:
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0
    assert cosine([3.0, 4.0], [3.0, 4.0]) == pytest.approx(1.0)
