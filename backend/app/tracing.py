"""Langfuse tracing (D-021). Modes, set by `TRACING_MODE`:

- `off` (default): nothing is exported.
- `full` (local development, evaluation runs): structure, timings, model, tokens, and inputs and
  outputs (question, prompt, answer, chunk texts).
- `metadata` (production traffic): structure, timings, model, tokens and allowlisted metadata;
  no text (CLAUDE.md, rule 5).

In `metadata` mode text is not passed to the SDK (`rag.tracing.text`), and `metadata_only_mask`
deletes, at export, every OpenTelemetry attribute that is not on an allowlist: an attribute added
by a future integration is dropped unless it is known to be safe.
"""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from functools import cache
from typing import Any, Literal

from rag.tracing import NOOP, Observation, ObservationType, Tracer

TracingMode = Literal["off", "full", "metadata"]

SAFE_ATTRIBUTES = frozenset(
    {
        "langfuse.trace.name",
        "langfuse.trace.tags",
        "langfuse.observation.type",
        "langfuse.observation.level",
        "langfuse.observation.model.name",
        "langfuse.observation.model.parameters",
        "langfuse.observation.usage_details",
        "langfuse.observation.cost_details",
        "langfuse.observation.completion_start_time",
        "langfuse.observation.prompt.name",
        "langfuse.observation.prompt.version",
        "langfuse.environment",
        "langfuse.release",
        "langfuse.version",
        "langfuse.internal.as_root",
        "langfuse.internal.is_app_root",
    }
)
SAFE_METADATA_KEYS = frozenset(
    {
        "kind",
        "route",
        "reason",
        "personal",
        "governing",
        "topic",
        "stage",
        "prompt_version",
        "abstain_reason",
        "removed_sentences",
        "removed_reasons",
        "kept_sentences",
        "retrieved_chunk_ids",
        "rule_ids",
        "sources_cited",
        "embedding_tokens",
        "lang",
    }
)
_METADATA_PREFIXES = ("langfuse.observation.metadata.", "langfuse.trace.metadata.")


def is_safe_attribute(key: str) -> bool:
    if key in SAFE_ATTRIBUTES:
        return True
    for prefix in _METADATA_PREFIXES:
        if key.startswith(prefix):
            return key.removeprefix(prefix).split(".", 1)[0] in SAFE_METADATA_KEYS
    return False


def metadata_only_mask(*, params: Any) -> Any:
    """`mask_otel_spans` for `metadata` mode: delete every attribute not on the allowlist."""
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    patches = {}
    for identifier, span in params.spans.items():
        unsafe = tuple(k for k in span.attributes if not is_safe_attribute(k))
        if unsafe:
            patches[identifier] = OtelSpanPatch(
                delete_attributes=unsafe, set_attributes={"permipath.masked": True}
            )
    return MaskOtelSpansResult(span_patches=patches)


ModelPrices = dict[str, tuple[float, float]]
"""Model name -> (input, output) USD per million tokens. Langfuse has no price for the Mistral
models used (cost empty on 2026-11-27), so costs are sent explicitly from the settings."""


def cost_details(model: str | None, usage: dict[str, int], prices: ModelPrices) -> dict[str, float]:
    if model not in prices:
        return {}
    price_in, price_out = prices[model]
    cost_in = usage.get("input_tokens", 0) * price_in / 1_000_000
    cost_out = usage.get("output_tokens", 0) * price_out / 1_000_000
    return {"input": cost_in, "output": cost_out, "total": cost_in + cost_out}


@dataclass
class LangfuseTracer:
    client: Any
    capture: bool
    prices: ModelPrices

    @contextmanager
    def observe(self, name: str, as_type: ObservationType, **fields: Any) -> Iterator[Observation]:
        if not self.capture:
            fields.pop("input", None)
            fields.pop("output", None)
        with self.client.start_as_current_observation(name=name, as_type=as_type, **fields) as obs:
            yield _Guarded(obs, self.capture, self.prices, fields.get("model"))


@dataclass
class _Guarded:
    """Drops input/output updates when text may not be recorded; adds cost to usage."""

    inner: Any
    capture: bool
    prices: ModelPrices
    model: str | None

    def update(self, **fields: Any) -> Any:
        if not self.capture:
            fields.pop("input", None)
            fields.pop("output", None)
        usage = fields.get("usage_details")
        if usage and "cost_details" not in fields:
            costs = cost_details(fields.get("model") or self.model, usage, self.prices)
            if costs:
                fields["cost_details"] = costs
        return self.inner.update(**fields)


@dataclass
class Tracing:
    """Entry point for one traced unit of work (one question)."""

    tracer: Tracer
    client: Any = None

    def run(
        self,
        name: str,
        work: Callable[[dict[str, Any]], dict[str, Any]],
        payload: dict[str, Any],
        *,
        tags: list[str],
        trace_input: Any,
        summarise: Callable[[dict[str, Any]], tuple[Any, dict[str, Any]]],
    ) -> tuple[dict[str, Any], str | None]:
        """Runs `work(payload)` under a root `agent` observation; returns its result and the
        trace id. `summarise(result)` gives the trace output (text) and safe metadata."""
        if self.client is None:
            return work(payload), None
        from langfuse import propagate_attributes

        with (
            propagate_attributes(trace_name=name, tags=tags),
            self.tracer.observe(name, "agent", input=trace_input) as root,
        ):
            result = work(payload)
            output, metadata = summarise(result)
            root.update(output=output, metadata=metadata)
            trace_id = getattr(getattr(root, "inner", None), "trace_id", None)
        return result, trace_id

    def score(self, trace_id: str, name: str, value: float, comment: str | None = None) -> None:
        if self.client is not None:
            self.client.create_score(trace_id=trace_id, name=name, value=value, comment=comment)

    def flush(self) -> None:
        if self.client is not None:
            self.client.flush()

    def shutdown(self) -> None:
        if self.client is not None:
            self.client.shutdown()


OFF = Tracing(NOOP)


def build_tracing(mode: TracingMode | None = None) -> Tracing:
    """From settings; `off` (or missing keys) gives a tracer that sends nothing."""
    from app.config import get_settings

    settings = get_settings()
    mode = mode or settings.tracing_mode
    if mode == "off":
        return OFF
    if settings.langfuse_public_key is None or settings.langfuse_secret_key is None:
        raise RuntimeError("TRACING_MODE is set but LANGFUSE_PUBLIC_KEY / SECRET_KEY are not")
    from langfuse import Langfuse

    client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key.get_secret_value(),
        base_url=settings.langfuse_base_url,
        environment=settings.langfuse_environment,
        mask_otel_spans=metadata_only_mask if mode == "metadata" else None,
    )
    from rag.answer import ANSWER_MODEL
    from rag.embeddings import EMBEDDING_MODEL

    prices: ModelPrices = {
        ANSWER_MODEL: (settings.price_chat_input_per_m, settings.price_chat_output_per_m),
        EMBEDDING_MODEL: (settings.price_embed_input_per_m, 0.0),
    }
    return Tracing(LangfuseTracer(client, capture=mode == "full", prices=prices), client)


@cache
def default_tracing() -> Tracing:
    """The process's tracing, built once from settings (like `get_settings`)."""
    return build_tracing()


def shutdown_default_tracing() -> None:
    """Flushes and stops tracing if it was built (server shutdown)."""
    if default_tracing.cache_info().currsize:
        default_tracing().shutdown()
