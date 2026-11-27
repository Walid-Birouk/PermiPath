"""`POST /ask`: runs the agent graph (`rag.graph`) on one question.

Privacy (CLAUDE.md, rule 5): the question is never stored or logged. One `query_logs` row per
request keeps the journey stage, the retrieved chunk ids, latency, token counts and eval flags
(response kind, abstain reason, number of removed sentences, governing source).
"""

import logging
import secrets
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, StringConstraints

from app.limits import DatabaseGuard, Guard, Prices
from app.tracing import Tracing
from ingestion.manifest import JourneyStage

log = logging.getLogger(__name__)
router = APIRouter()

Question = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=1000)]


class AskRequest(BaseModel):
    question: Question
    lang: Literal["fr", "en"] | None = None
    """Answer language; detected from the question when absent."""
    stage: JourneyStage | None = None
    nationality: Literal["algerian", "other", "unknown"] = "unknown"


class AnswerSentenceOut(BaseModel):
    text: str
    sources: list[int] = Field(description="Numbers of the sources in `sources`.")


class SourceOut(BaseModel):
    n: int
    ref: str
    title: str
    url: str
    source_type: str
    authority: str
    in_force_on: str
    in_force_from: str | None
    checked_at: str | None
    rule: str | None = Field(default=None, description="Rules-table row behind this source.")


class AskResponse(BaseModel):
    kind: Literal["answer", "refuse", "handoff", "abstain"]
    lang: Literal["fr", "en"]
    sentences: list[AnswerSentenceOut]
    sources: list[SourceOut]
    notices: list[str] = Field(description="Always starts with the 'not legal advice' notice.")
    governing: Literal["agreement", "general"] | None = None
    reason: str | None = None
    abstain_reason: str | None = None


Agent = Callable[[dict[str, Any]], dict[str, Any]]
QueryLogger = Callable[[dict[str, Any]], None]


TRACE_NAME = "answer-question"


def summarise(state: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    """Trace output (text, recorded only in `full` mode) and safe trace metadata."""
    response = state["response"]
    checked = state.get("checked")
    output = {
        "kind": response["kind"],
        "sentences": [s["text"] for s in response["sentences"]],
        "sources": [s["ref"] for s in response["sources"]],
    }
    metadata = {
        "kind": response["kind"],
        "governing": response.get("governing"),
        "reason": response.get("reason"),
        "abstain_reason": response.get("abstain_reason"),
        "removed_sentences": len(checked.dropped) if checked else 0,
        "sources_cited": [s["ref"] for s in response["sources"]],
    }
    return output, metadata


def make_agent(
    rules: Literal["verified", "all", "none"] = "verified",
    tracing: Tracing | None = None,
    tags: tuple[str, ...] = ("api",),
) -> Agent:
    """The agent graph wired to the database, Mistral and (optionally) Langfuse. `rules`: rows
    of the rules table the agent may use (`all` includes unverified rows, for evals). The
    returned state carries `trace_id` when the question was traced."""
    from app.config import get_settings
    from app.tracing import OFF
    from db.session import make_engine
    from rag.answer import MistralAnswerer
    from rag.embeddings import MistralEmbedder
    from rag.graph import Deps, build_graph
    from rag.retrieval import HybridRetriever
    from rag.rules import RulesTool
    from rag.tracing import TracedEmbedder, text

    tracing = tracing or OFF
    settings = get_settings()
    if settings.mistral_api_key is None:
        raise RuntimeError("MISTRAL_API_KEY is not set")
    key = settings.mistral_api_key.get_secret_value()
    engine = make_engine(settings.database_url)
    embedder = TracedEmbedder(MistralEmbedder(key), tracing.tracer)
    retriever = HybridRetriever(engine, embedder)
    tool = None if rules == "none" else RulesTool(engine, include_unverified=rules == "all")
    graph = build_graph(Deps(retriever, MistralAnswerer(key), tool, tracing.tracer))

    def run(request: dict[str, Any]) -> dict[str, Any]:
        trace_input = text(
            tracing.tracer,
            {k: request.get(k) for k in ("question", "lang", "nationality")},
        )
        state, trace_id = tracing.run(
            TRACE_NAME,
            graph.invoke,
            request,
            tags=list(tags),
            trace_input=trace_input,
            summarise=summarise,
        )
        return {**state, "trace_id": trace_id}

    return run


@cache
def _default_agent(rules: Literal["verified", "all", "none"] = "verified") -> Agent:
    """The API's agent; traced as `TRACING_MODE` says (`metadata` in production)."""
    from app.tracing import default_tracing

    return make_agent(rules, default_tracing())


def get_agent() -> Agent:
    return _default_agent()


def _write_log(entry: dict[str, Any]) -> None:
    from sqlalchemy.orm import Session

    from app.config import get_settings
    from db.models import QueryLog
    from db.session import make_engine

    engine = make_engine(get_settings().database_url)
    try:
        with Session(engine) as session, session.begin():
            session.add(QueryLog(**entry))
    finally:
        engine.dispose()


def get_query_logger() -> QueryLogger:
    return _write_log


def log_entry(state: dict[str, Any], stage: str | None, latency_ms: int) -> dict[str, Any]:
    """Metadata-only log row (rule 5)."""
    response = state["response"]
    found = state.get("search")
    draft = state.get("draft")
    checked = state.get("checked")
    return {
        "journey_stage": stage,
        "retrieved_chunk_ids": [c.id for c in found.chunks] if found else [],
        "latency_ms": latency_ms,
        "prompt_tokens": draft.prompt_tokens if draft else None,
        "completion_tokens": draft.completion_tokens if draft else None,
        "eval_flags": {
            "kind": response["kind"],
            "abstain_reason": response.get("abstain_reason"),
            "removed_sentences": len(checked.dropped) if checked else 0,
            "governing": response.get("governing"),
            "embedding_tokens": found.embedding_tokens if found else 0,
        },
    }


@cache
def _default_guard() -> Guard:
    from app.config import get_settings
    from db.session import make_engine

    settings = get_settings()
    secret = (
        settings.ip_hash_secret.get_secret_value().encode()
        if settings.ip_hash_secret
        else secrets.token_bytes(32)
    )
    prices = Prices(
        settings.price_chat_input_per_m,
        settings.price_chat_output_per_m,
        settings.price_embed_input_per_m,
    )
    return DatabaseGuard(
        make_engine(settings.database_url),
        settings.daily_question_limit,
        settings.monthly_spend_cap_eur,
        prices,
        secret,
    )


def get_guard() -> Guard:
    return _default_guard()


def client_ip(http_request: Request) -> str:
    from app.config import get_settings

    if get_settings().trust_forwarded_for:
        forwarded = http_request.headers.get("x-forwarded-for", "")
        if first := forwarded.split(",")[0].strip():
            return first
    return http_request.client.host if http_request.client else "unknown"


@router.post(
    "/ask",
    response_model=AskResponse,
    responses={
        429: {"description": "Daily question limit reached for this visitor (`Retry-After`)."},
        503: {"description": "Monthly spend cap reached; paused until next month."},
    },
)
def ask(
    request: AskRequest,
    http_request: Request,
    agent: Annotated[Agent, Depends(get_agent)],
    query_logger: Annotated[QueryLogger, Depends(get_query_logger)],
    guard: Annotated[Guard, Depends(get_guard)],
) -> AskResponse:
    admission = guard.admit(client_ip(http_request))
    if admission.outcome != "ok":
        status = 429 if admission.outcome == "rate_limited" else 503
        raise HTTPException(
            status, admission.outcome, headers={"Retry-After": str(admission.retry_after_s)}
        )
    started = time.perf_counter()
    state = agent(
        {
            "question": request.question,
            "nationality": request.nationality,
            "on": datetime.now(UTC).date(),
            **({"lang": request.lang} if request.lang else {}),
        }
    )
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        query_logger(log_entry(state, request.stage, latency_ms))
    except Exception:  # logging must not fail the answer
        log.exception("query log write failed")
    return AskResponse.model_validate(state["response"])
