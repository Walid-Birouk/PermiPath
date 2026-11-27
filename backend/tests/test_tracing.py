"""Tracing (D-021): allowlist mask, no text without capture, one typed observation per node."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import pytest
from langfuse.types import MaskOtelSpansParams, OtelSpanData, OtelSpanIdentifier

from app.tracing import LangfuseTracer, cost_details, is_safe_attribute, metadata_only_mask
from rag.graph import Deps, build_graph
from rag.tracing import NOOP, TracedEmbedder, text
from tests.test_graph import ON, SHEET, SHEET_2, SUPPORTED, FakeAnswerer, FakeRetriever

QUESTION = "Quelle est la durée de la carte fictive de Zorglub ?"
SPAN_ID = OtelSpanIdentifier(trace_id="t", span_id="s")


@pytest.mark.parametrize(
    ("key", "safe"),
    [
        ("langfuse.observation.type", True),
        ("langfuse.observation.model.name", True),
        ("langfuse.observation.usage_details", True),
        ("langfuse.observation.metadata.kind", True),
        ("langfuse.trace.metadata.retrieved_chunk_ids", True),
        ("langfuse.observation.input", False),
        ("langfuse.observation.output", False),
        ("langfuse.observation.status_message", False),
        ("langfuse.observation.metadata.question", False),
        ("gen_ai.prompt.0.content", False),
        ("user.id", False),
    ],
)
def test_allowlist(key: str, safe: bool) -> None:
    assert is_safe_attribute(key) is safe


def _snapshot(attributes: dict[str, Any]) -> OtelSpanData:
    return OtelSpanData(
        trace_id="t",
        span_id="s",
        parent_span_id=None,
        name="generate-answer",
        instrumentation_scope_name=None,
        instrumentation_scope_version=None,
        attributes=MappingProxyType(attributes),
        resource_attributes=MappingProxyType({}),
    )


def test_mask_deletes_every_unsafe_attribute() -> None:
    span = _snapshot(
        {
            "langfuse.observation.type": "generation",
            "langfuse.observation.input": QUESTION,
            "langfuse.observation.metadata.kind": "answer",
            "some.future.integration.text": QUESTION,
        }
    )
    result = metadata_only_mask(params=MaskOtelSpansParams(spans=MappingProxyType({SPAN_ID: span})))
    patch = result.span_patches[SPAN_ID]
    assert set(patch.delete_attributes) == {
        "langfuse.observation.input",
        "some.future.integration.text",
    }
    clean = _snapshot({"langfuse.observation.type": "span"})
    assert (
        metadata_only_mask(
            params=MaskOtelSpansParams(spans=MappingProxyType({SPAN_ID: clean}))
        ).span_patches
        == {}
    )


@dataclass
class FakeObservation:
    record: dict[str, Any]

    def update(self, **fields: Any) -> None:
        self.record.setdefault("updates", []).append(fields)


@dataclass
class FakeClient:
    observations: list[dict[str, Any]] = field(default_factory=list)

    @contextmanager
    def start_as_current_observation(self, **fields: Any) -> Iterator[FakeObservation]:
        record = dict(fields)
        self.observations.append(record)
        yield FakeObservation(record)


def _fields(record: dict[str, Any]) -> dict[str, Any]:
    merged = {k: v for k, v in record.items() if k != "updates"}
    for update in record.get("updates", []):
        merged.update(update)
    return merged


PRICES = {"fake-chat": (1.0, 2.0)}


@pytest.mark.parametrize("capture", [True, False])
def test_graph_emits_one_typed_observation_per_node(capture: bool) -> None:
    client = FakeClient()
    tracer = LangfuseTracer(client, capture=capture, prices=PRICES)
    graph = build_graph(
        Deps(FakeRetriever([SHEET, SHEET_2]), FakeAnswerer(SUPPORTED), None, tracer)
    )
    graph.invoke({"question": QUESTION, "on": ON})
    names = [(o["name"], o["as_type"]) for o in client.observations]
    assert names == [
        ("classify-scope", "guardrail"),
        ("retrieve-sources", "retriever"),
        ("generate-answer", "generation"),
        ("check-citations", "guardrail"),
    ]
    merged = [_fields(o) for o in client.observations]
    retrieved = merged[1]["metadata"]["retrieved_chunk_ids"]
    assert retrieved == ["fact_sheet:F1:0", "fact_sheet:F2:0"]
    exported = repr(client.observations)
    if capture:
        assert QUESTION in exported
        assert merged[0]["input"] == QUESTION
    else:
        assert "Zorglub" not in exported
        assert all("input" not in m and "output" not in m for m in merged)


def test_costs_are_added_from_configured_prices() -> None:
    assert cost_details(
        "fake-chat", {"input_tokens": 1_000_000, "output_tokens": 500_000}, PRICES
    ) == {
        "input": 1.0,
        "output": 1.0,
        "total": 2.0,
    }
    assert cost_details("unknown", {"input_tokens": 5}, PRICES) == {}
    client = FakeClient()
    tracer = LangfuseTracer(client, capture=False, prices=PRICES)
    with tracer.observe("generate-answer", "generation") as obs:
        obs.update(model="fake-chat", usage_details={"input_tokens": 1_000_000})
    assert _fields(client.observations[0])["cost_details"]["total"] == 1.0


def test_noop_tracer_and_text_helper() -> None:
    assert text(NOOP, "secret") is None
    with NOOP.observe("x", "span", input="secret") as obs:
        obs.update(output="secret")


@dataclass
class Batch:
    vectors: list[list[float]]
    prompt_tokens: int


class Embedder:
    model = "fake-embed"

    def embed(self, texts: Any) -> Batch:
        return Batch([[0.0]], 3)


def test_traced_embedder_records_model_and_usage_without_text() -> None:
    client = FakeClient()
    embedder = TracedEmbedder(Embedder(), LangfuseTracer(client, capture=False, prices={}))
    assert embedder.embed([QUESTION]).prompt_tokens == 3
    record = _fields(client.observations[0])
    assert (record["name"], record["as_type"], record["model"]) == (
        "embed-question",
        "embedding",
        "fake-embed",
    )
    assert record["usage_details"] == {"input_tokens": 3}
    assert QUESTION not in repr(client.observations)
