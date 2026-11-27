"""Text embeddings behind a provider interface (CLAUDE.md, Stack: Mistral behind a thin
provider interface). Used by ingestion to fill `chunks.embedding` and, later, by retrieval to
embed queries with the same model.

Mistral endpoint (official documentation, docs.mistral.ai, read 2026-11-27):
`POST https://api.mistral.ai/v1/embeddings`, header `Authorization: Bearer <key>`, body
`{"model": "mistral-embed", "input": [str, ...]}`; response `data[]` items carry `embedding`
(1024 floats for `mistral-embed`) and a 0-based `index`, and `usage.prompt_tokens` counts the
billed input tokens.

UNVERIFIED: the documentation states no limit on inputs or tokens per request and no rate-limit
behaviour. Callers keep requests small (see `ingestion.embed`), and this client retries HTTP 429
and 5xx responses with backoff, honouring `Retry-After` when present.
"""

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

import httpx2

from db.models import EMBEDDING_DIM

EMBEDDING_MODEL = "mistral-embed"
MISTRAL_API_URL = "https://api.mistral.ai/v1"

_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_RETRY_DELAY_S = 60.0
_BODY_EXCERPT = 300


@dataclass(frozen=True)
class EmbeddingBatch:
    vectors: list[list[float]]
    """One vector per input text, in input order."""
    prompt_tokens: int
    """Input tokens billed for the request, as reported by the provider."""


class Embedder(Protocol):
    """Embeds texts with one fixed model. Query and passage embeddings must share it."""

    @property
    def model(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch: ...


class EmbeddingError(Exception):
    """Provider call failed or returned an unexpected payload. Messages never contain the key."""


class MistralEmbedder:
    """`Embedder` over the Mistral embeddings REST endpoint."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = MISTRAL_API_URL,
        model: str = EMBEDDING_MODEL,
        http: httpx2.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
        max_retries: int = 4,
        timeout_s: float = 60.0,
    ) -> None:
        self._api_key = api_key
        self._url = f"{base_url.rstrip('/')}/embeddings"
        self._model = model
        self._http = http or httpx2.Client(timeout=timeout_s)
        self._sleep = sleep
        self._max_retries = max_retries

    @property
    def model(self) -> str:
        return self._model

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "MistralEmbedder":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        if not texts:
            return EmbeddingBatch(vectors=[], prompt_tokens=0)
        payload = self._post({"model": self._model, "input": list(texts)})
        return _parse(payload, len(texts))

    def _post(self, body: dict[str, Any]) -> Any:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        response = self._http.post(self._url, json=body, headers=headers)
        for attempt in range(self._max_retries):
            if response.status_code not in _RETRY_STATUSES:
                break
            self._sleep(_retry_delay(response, attempt))
            response = self._http.post(self._url, json=body, headers=headers)
        if response.status_code != 200:
            raise EmbeddingError(
                f"POST /embeddings -> HTTP {response.status_code}: {response.text[:_BODY_EXCERPT]}"
            )
        return response.json()


def _parse(payload: Any, expected: int) -> EmbeddingBatch:
    try:
        items = sorted(payload["data"], key=lambda item: item["index"])
        indexes = [item["index"] for item in items]
        vectors = [[float(x) for x in item["embedding"]] for item in items]
        tokens = int(payload["usage"]["prompt_tokens"])
    except (KeyError, TypeError, ValueError) as exc:
        raise EmbeddingError(f"unexpected embeddings payload: {exc!r}") from exc
    if indexes != list(range(expected)):
        raise EmbeddingError(f"expected {expected} embeddings, got indexes {indexes[:10]}")
    if any(len(v) != EMBEDDING_DIM for v in vectors):
        raise EmbeddingError(f"expected {EMBEDDING_DIM}-dimensional embeddings")
    return EmbeddingBatch(vectors=vectors, prompt_tokens=tokens)


def _retry_delay(response: httpx2.Response, attempt: int) -> float:
    """`Retry-After` seconds when present and valid, else 1, 2, 4, 8... seconds; capped."""
    header = response.headers.get("Retry-After", "")
    delay = float(header) if header.isdigit() else float(2**attempt)
    return min(delay, _MAX_RETRY_DELAY_S)
