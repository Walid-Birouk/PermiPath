"""Mistral embeddings client (`rag.embeddings`) against a mocked transport. Response payloads
follow the layout of the example in the official documentation (docs.mistral.ai, text
embeddings); vectors are fake."""

import json
from typing import Any

import httpx2
import pytest

from db.models import EMBEDDING_DIM
from rag.embeddings import EMBEDDING_MODEL, EmbeddingError, MistralEmbedder

API_KEY = "test-key-not-real"


def _vector(marker: float) -> list[float]:
    return [marker] * EMBEDDING_DIM


def _payload(vectors: list[list[float]], order: list[int] | None = None) -> dict[str, Any]:
    order = order if order is not None else list(range(len(vectors)))
    return {
        "id": "embd-test",
        "object": "list",
        "data": [{"object": "embedding", "embedding": vectors[i], "index": i} for i in order],
        "model": EMBEDDING_MODEL,
        "usage": {"prompt_tokens": 7, "total_tokens": 7, "completion_tokens": 0},
    }


def _client(
    handler: Any, delays: list[float] | None = None, max_retries: int = 4
) -> MistralEmbedder:
    return MistralEmbedder(
        API_KEY,
        http=httpx2.Client(transport=httpx2.MockTransport(handler)),
        sleep=(delays if delays is not None else []).append,
        max_retries=max_retries,
    )


def test_embed_sends_model_inputs_and_bearer_key() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=_payload([_vector(0.1), _vector(0.2)]))

    batch = _client(handler).embed(["un", "deux"])
    request = seen[0]
    assert (request.method, str(request.url)) == ("POST", "https://api.mistral.ai/v1/embeddings")
    assert request.headers["Authorization"] == f"Bearer {API_KEY}"
    assert json.loads(request.content) == {"model": "mistral-embed", "input": ["un", "deux"]}
    assert [v[0] for v in batch.vectors] == [0.1, 0.2]
    assert batch.prompt_tokens == 7


def test_vectors_follow_input_order_whatever_the_response_order() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        vectors = [_vector(0.1), _vector(0.2), _vector(0.3)]
        return httpx2.Response(200, json=_payload(vectors, order=[2, 0, 1]))

    batch = _client(handler).embed(["a", "b", "c"])
    assert [v[0] for v in batch.vectors] == [0.1, 0.2, 0.3]


def test_empty_input_makes_no_request() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        raise AssertionError("no request expected")

    assert _client(handler).embed([]).vectors == []


@pytest.mark.parametrize("status", [429, 503])
def test_transient_errors_are_retried_with_backoff(status: int) -> None:
    responses = [httpx2.Response(status), httpx2.Response(status, headers={"Retry-After": "3"})]

    def handler(_: httpx2.Request) -> httpx2.Response:
        return responses.pop(0) if responses else httpx2.Response(200, json=_payload([_vector(1)]))

    delays: list[float] = []
    assert _client(handler, delays).embed(["x"]).vectors == [_vector(1)]
    assert delays == [1.0, 3.0]


def test_persistent_rate_limit_fails_after_the_retries() -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(429, json={"message": "rate limited"})

    delays: list[float] = []
    with pytest.raises(EmbeddingError, match="HTTP 429"):
        _client(handler, delays, max_retries=2).embed(["x"])
    assert delays == [1.0, 2.0]


def test_error_message_never_contains_the_key() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, json={"message": "Unauthorized"})

    with pytest.raises(EmbeddingError, match="HTTP 401") as exc_info:
        _client(handler).embed(["x"])
    assert API_KEY not in str(exc_info.value)


@pytest.mark.parametrize(
    "payload",
    [
        _payload([_vector(0.1)]),  # one vector for two inputs
        _payload([[0.1, 0.2], [0.3, 0.4]]),  # wrong dimension
        {"data": []},  # no usage, no vectors
    ],
)
def test_unexpected_payloads_are_rejected(payload: dict[str, Any]) -> None:
    def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, json=payload)

    with pytest.raises(EmbeddingError):
        _client(handler).embed(["a", "b"])
