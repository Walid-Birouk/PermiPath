"""Model comparison over the index's chunks (`evals.model_comparison`) with a fake encoder."""

from collections.abc import Sequence
from pathlib import Path

import pytest

from evals.model_comparison import IndexModelRetriever, embed_index, load_cache, save_cache

DIM = 4


class CountingEncoder:
    """Axis 1 for texts containing 'travail', axis 0 otherwise; counts encoded texts."""

    model = "fake-encoder"

    def __init__(self) -> None:
        self.encoded: list[str] = []

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        self.encoded.extend(texts)
        return [[0.0, 1.0, 0.0, 0.0] if "travail" in t else [1.0, 0.0, 0.0, 0.0] for t in texts]


ROWS = [
    ("law:L1:0", "law:L1", "h1", "Article L1", "FAKE séjour"),
    ("fact_sheet:F1:0", "fact_sheet:F1", "h2", "Fiche F1", "FAKE travail"),
]


def test_embeds_title_and_text_once_then_uses_the_cache(tmp_path: Path) -> None:
    encoder = CountingEncoder()
    cache: dict[str, dict[str, object]] = {}
    _, embedded = embed_index(ROWS, encoder, cache)
    assert embedded == 2
    assert encoder.encoded == ["Article L1\n\nFAKE séjour", "Fiche F1\n\nFAKE travail"]
    path = tmp_path / "cache.json"
    save_cache(path, encoder.model, cache)
    reloaded = load_cache(path, encoder.model)
    _, embedded = embed_index(ROWS, encoder, reloaded)
    assert embedded == 0
    assert load_cache(path, "other-model") == {}


def test_changed_chunk_is_re_embedded_and_removed_chunk_dropped() -> None:
    encoder = CountingEncoder()
    cache: dict[str, dict[str, object]] = {}
    embed_index(ROWS, encoder, cache)
    changed = [(*ROWS[0][:2], "h1-new", *ROWS[0][3:])]
    _, embedded = embed_index(changed, encoder, cache)
    assert embedded == 1
    assert set(cache) == {"law:L1:0"}


def test_retriever_ranks_by_cosine_across_source_types() -> None:
    encoder = CountingEncoder()
    chunks, _ = embed_index(ROWS, encoder, {})
    retriever = IndexModelRetriever(chunks, encoder)
    retrieval = retriever.retrieve("question travail", depth=2)
    assert [h.source_ref for h in retrieval.hits] == ["fact_sheet:F1", "law:L1"]
    assert retrieval.hits[0].score == pytest.approx(1.0)
    assert retriever.describe()["chunks_by_source_type"] == {"fact_sheet": 1, "law": 1}
