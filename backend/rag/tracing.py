"""Tracing interface used by the graph nodes (implementation: `app.tracing`, Langfuse; D-021).

Nodes open one typed observation each (`retriever`, `generation`, `guardrail`, ...). `capture`
says whether text (question, prompts, answers, chunk texts) may be recorded: true in development
and evaluation runs, false on production traffic (CLAUDE.md, rule 5). Callers pass text through
`text()`, which drops it when capture is off. Without a tracer, `NOOP` does nothing.
"""

from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Literal, Protocol

ObservationType = Literal[
    "span",
    "generation",
    "agent",
    "tool",
    "chain",
    "retriever",
    "embedding",
    "guardrail",
    "evaluator",
]


class Observation(Protocol):
    def update(self, **fields: Any) -> Any: ...


class Tracer(Protocol):
    capture: bool

    def observe(
        self, name: str, as_type: ObservationType, **fields: Any
    ) -> AbstractContextManager[Observation]: ...


class _NoopObservation:
    def update(self, **fields: Any) -> None:
        return None


class NoopTracer:
    capture = False

    @contextmanager
    def observe(self, name: str, as_type: ObservationType, **fields: Any) -> Iterator[Observation]:
        yield _NoopObservation()


NOOP = NoopTracer()


def text(tracer: Tracer, value: Any) -> Any:
    """`value` when the tracer may record text, else None."""
    return value if tracer.capture else None


class _Embedder(Protocol):
    @property
    def model(self) -> str: ...

    def embed(self, texts: Sequence[str]) -> Any: ...


class TracedEmbedder:
    """Records each embedding call as an `embedding` observation (model, tokens)."""

    def __init__(self, inner: _Embedder, tracer: Tracer) -> None:
        self._inner = inner
        self._tracer = tracer

    @property
    def model(self) -> str:
        return self._inner.model

    def embed(self, texts: Sequence[str]) -> Any:
        with self._tracer.observe(
            "embed-question",
            "embedding",
            model=self._inner.model,
            input=text(self._tracer, list(texts)),
        ) as obs:
            batch = self._inner.embed(texts)
            obs.update(usage_details={"input_tokens": batch.prompt_tokens})
        return batch
