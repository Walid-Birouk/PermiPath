"""`POST /ask`: runs the agent graph (`rag.graph`) on one question.

Privacy (CLAUDE.md, rule 5): the question is never stored or logged. One `query_logs` row per
request keeps the journey stage, the retrieved chunk ids, latency, token counts and eval flags
(response kind, abstain reason, number of removed sentences, governing source).
"""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from functools import cache
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, StringConstraints

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


@cache
def _default_agent() -> Agent:
    from app.config import get_settings
    from db.session import make_engine
    from rag.answer import MistralAnswerer
    from rag.embeddings import MistralEmbedder
    from rag.graph import Deps, build_graph
    from rag.retrieval import HybridRetriever

    settings = get_settings()
    if settings.mistral_api_key is None:
        raise RuntimeError("MISTRAL_API_KEY is not set")
    key = settings.mistral_api_key.get_secret_value()
    retriever = HybridRetriever(make_engine(settings.database_url), MistralEmbedder(key))
    graph = build_graph(Deps(retriever, MistralAnswerer(key)))
    return graph.invoke  # type: ignore[no-any-return]


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


@router.post("/ask", response_model=AskResponse)
def ask(
    request: AskRequest,
    agent: Annotated[Agent, Depends(get_agent)],
    query_logger: Annotated[QueryLogger, Depends(get_query_logger)],
) -> AskResponse:
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
