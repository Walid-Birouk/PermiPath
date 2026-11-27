"""Retrieval as the app runs it: hybrid retrieval (D-016) followed by precedence (D-017).

Shared by the `retrieve` node of the graph and the `hybrid-precedence` eval pipeline, so the
evaluation measures the code that answers users.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from rag import precedence
from rag.retrieval import Filters, RetrievalResult, RetrievedChunk

DEPTH = 10


class Retriever(Protocol):
    def retrieve(
        self,
        question: str,
        filters: Filters,
        depth: int = 10,
        query_vector: Sequence[float] | None = None,
    ) -> RetrievalResult: ...


@dataclass(frozen=True)
class SearchResult:
    chunks: list[RetrievedChunk]
    """Best first, after precedence; at most `depth`."""
    decision: precedence.PrecedenceDecision
    embedding_tokens: int


def search(
    retriever: Retriever, question: str, algerian: bool, on: date, depth: int = DEPTH
) -> SearchResult:
    scopes = ("all", "algerian") if algerian else ("all",)
    decision = precedence.decide(question, algerian)
    result = retriever.retrieve(
        question, Filters(nationality_scopes=scopes, in_force_on=on), depth=depth * 2
    )
    chunks = result.chunks
    tokens = result.embedding_tokens
    if decision.governing == "agreement":
        governing = retriever.retrieve(
            question,
            Filters(
                nationality_scopes=scopes, source_types=precedence.AGREEMENT_FIRST, in_force_on=on
            ),
            depth=precedence.GOVERNING_CANDIDATES,
            query_vector=result.query_vector,
        )
        chunks = precedence.merge(chunks, governing.chunks)
        tokens += governing.embedding_tokens
    return SearchResult(precedence.order(chunks, decision)[:depth], decision, tokens)
