from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from evals.grid import Passage, TokenCountingEncoder, embed_passages


@dataclass
class FakeEncoder:
    model: str = "fake-1"
    calls: list[list[str]] = field(default_factory=list)

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [[float(len(t)), 1.0] for t in texts]


@dataclass
class FakeBatch:
    vectors: list[list[float]]
    prompt_tokens: int


@dataclass
class FakeEmbedder:
    model: str = "api-1"
    requests: list[list[str]] = field(default_factory=list)

    def embed(self, texts: Sequence[str]) -> FakeBatch:
        self.requests.append(list(texts))
        return FakeBatch([[1.0, 0.0] for _ in texts], prompt_tokens=10 * len(texts))


def _passage(chunk_id: str, text: str, vector: list[float] | None = None) -> Passage:
    return Passage(chunk_id, f"fact_sheet:{chunk_id}", f"h-{text}", text, vector)


def test_passages_are_encoded_once_then_read_from_the_cache(tmp_path: Path) -> None:
    cache = tmp_path / "grid.json"
    encoder = FakeEncoder()
    passages = [_passage("a", "aa"), _passage("b", "bbb")]
    first, encoded = embed_passages(passages, encoder, cache)
    assert encoded == 2
    assert [e.vector for e in first] == [[2.0, 1.0], [3.0, 1.0]]
    again, encoded = embed_passages(passages, encoder, cache)
    assert encoded == 0
    assert [e.vector for e in again] == [e.vector for e in first]
    assert len(encoder.calls) == 1


def test_changed_text_or_other_model_is_re_encoded(tmp_path: Path) -> None:
    cache = tmp_path / "grid.json"
    embed_passages([_passage("a", "aa")], FakeEncoder(), cache)
    _, encoded = embed_passages([_passage("a", "changed")], FakeEncoder(), cache)
    assert encoded == 1
    _, encoded = embed_passages([_passage("a", "changed")], FakeEncoder(model="fake-2"), cache)
    assert encoded == 1


def test_known_vectors_are_used_as_is(tmp_path: Path) -> None:
    encoder = FakeEncoder()
    embedded, encoded = embed_passages([_passage("a", "aa", [0.5, 0.5])], encoder, tmp_path / "c")
    assert encoded == 0 and not encoder.calls
    assert embedded[0].vector == [0.5, 0.5]


def test_api_encoder_batches_by_count_and_size_and_counts_tokens() -> None:
    api = FakeEmbedder()
    encoder = TokenCountingEncoder(api, max_inputs=2, max_chars=10)
    vectors = encoder.encode(["aaaa", "bbbb", "cccc", "dddddddd", "e"])
    assert len(vectors) == 5
    assert api.requests == [["aaaa", "bbbb"], ["cccc"], ["dddddddd", "e"]]
    assert encoder.tokens == 50
    assert encoder.model == "api-1"
