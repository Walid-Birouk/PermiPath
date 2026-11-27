"""The PermiPath agent as a LangGraph `StateGraph` (diagram in docs/architecture.md).

    route ──out of scope──▶ refuse
      │  ──handoff───────▶ handoff
      ▼
    clarify ─▶ eligibility ─▶ retrieve ──nothing found──▶ abstain
                  │
                  ▼
                answer ─▶ check_citations ──no supported sentence──▶ abstain
                                 │
                                 ▼
                              respond

Decision logic lives in project modules (`guardrails.scope`, `guardrails.nationality`,
`rag.search`, `rag.rules`, `rag.citations`, `rag.abstain`); the nodes only wire them. The
model, the retriever and the rules tool are injected (`Deps`), so every path is tested with
fakes. `eligibility` looks up verified rows of the rules table (build step 5); the model may cite
them like sources. Every response carries the "not legal advice" notice.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph

from guardrails import texts
from guardrails.nationality import detect_nationality
from guardrails.scope import ScopeDecision, classify
from rag.abstain import abstain_reason
from rag.answer import Answerer, DraftAnswer
from rag.citations import CheckedAnswer, check
from rag.retrieval import RetrievedChunk
from rag.rules import RuleRow
from rag.search import Retriever, SearchResult, search
from rag.tracing import NOOP, Tracer, text

Kind = Literal["answer", "refuse", "handoff", "abstain"]
Nationality = Literal["algerian", "other", "unknown"]


class AskState(TypedDict, total=False):
    # Request
    question: str
    lang: Literal["fr", "en"]
    nationality: Nationality
    on: date
    # Filled by the nodes
    scope: ScopeDecision
    rules: list[RuleRow]
    search: SearchResult
    draft: DraftAnswer
    checked: CheckedAnswer
    abstain_reason: str
    response: dict[str, Any]


class RulesLookup(Protocol):
    def lookup(self, question: str, algerian: bool) -> list[RuleRow]: ...


@dataclass(frozen=True)
class Deps:
    retriever: Retriever
    answerer: Answerer
    rules: RulesLookup | None = None
    """None: no rules table (the `eligibility` node returns no row)."""
    tracer: Tracer = NOOP
    """One typed observation per node (D-021); text recorded only when `tracer.capture`."""


def _notices(state: AskState, *names: str) -> list[str]:
    return [texts.notice(n, state["lang"]) for n in ("not_legal_advice", *names)]


def _source(chunk: RetrievedChunk, number: int, on: date) -> dict[str, Any]:
    return {
        "n": number,
        "ref": chunk.source_ref,
        "title": chunk.title,
        "url": chunk.url,
        "source_type": chunk.source_type,
        "authority": chunk.authority,
        "in_force_on": on.isoformat(),
        "in_force_from": chunk.in_force_from.isoformat() if chunk.in_force_from else None,
        "checked_at": chunk.fetched_at.date().isoformat() if chunk.fetched_at else None,
        "rule": None,
    }


def _rule_source(rule: RuleRow, number: int, on: date) -> dict[str, Any]:
    """A cited rule is shown as the official source it quotes."""
    checked = rule.source_checked_at
    return {
        "n": number,
        "ref": rule.source_ref,
        "title": rule.source_title,
        "url": rule.url,
        "source_type": rule.source_type,
        "authority": "official",
        "in_force_on": on.isoformat(),
        "in_force_from": None,
        "checked_at": checked.date().isoformat() if checked else None,
        "rule": rule.id,
    }


def build_graph(deps: Deps) -> Any:
    tracer = deps.tracer

    def route(state: AskState) -> AskState:
        with tracer.observe(
            "classify-scope", "guardrail", input=text(tracer, state["question"])
        ) as obs:
            scope = classify(state["question"])
            lang = state.get("lang") or texts.detect_lang(state["question"])
            obs.update(
                output={"route": scope.route, "reason": scope.reason},
                metadata={
                    "route": scope.route,
                    "reason": scope.reason,
                    "personal": scope.personal,
                    "lang": lang,
                },
            )
        return {"scope": scope, "lang": lang}

    def after_route(state: AskState) -> str:
        return {"out_of_scope": "refuse", "handoff": "handoff"}.get(state["scope"].route, "clarify")

    def clarify(state: AskState) -> AskState:
        given = state.get("nationality", "unknown")
        if given == "unknown" and detect_nationality(state["question"]) == "algerian":
            given = "algerian"
        return {"nationality": given}

    def eligibility(state: AskState) -> AskState:
        if deps.rules is None:
            return {"rules": []}
        with tracer.observe("lookup-rules", "tool", input=text(tracer, state["question"])) as obs:
            rows = deps.rules.lookup(state["question"], state["nationality"] == "algerian")
            obs.update(
                output=text(tracer, [{"id": r.id, "value": r.value} for r in rows]),
                metadata={"rule_ids": [r.id for r in rows]},
            )
        return {"rules": rows}

    def retrieve(state: AskState) -> AskState:
        with tracer.observe(
            "retrieve-sources", "retriever", input=text(tracer, state["question"])
        ) as obs:
            found = search(
                deps.retriever, state["question"], state["nationality"] == "algerian", state["on"]
            )
            obs.update(
                output=text(
                    tracer,
                    [
                        {"id": c.id, "title": c.title, "score": round(c.score, 4), "text": c.text}
                        for c in found.chunks
                    ],
                ),
                metadata={
                    "retrieved_chunk_ids": [c.id for c in found.chunks],
                    "governing": found.decision.governing,
                    "topic": found.decision.topic,
                    "embedding_tokens": found.embedding_tokens,
                },
            )
        return {"search": found}

    def after_retrieve(state: AskState) -> str:
        return "answer" if state["search"].chunks else "abstain"

    def answer(state: AskState) -> AskState:
        found = state["search"]
        with tracer.observe("generate-answer", "generation") as obs:
            draft = deps.answerer.answer(
                state["question"],
                state["lang"],
                found.chunks,
                found.decision,
                state.get("rules", []),
            )
            obs.update(
                model=draft.model or None,
                input=text(tracer, [{"role": "user", "content": draft.prompt}]),
                output=text(
                    tracer,
                    {
                        "declined": draft.declined,
                        "sentences": [
                            {"text": s.text, "source_ids": list(s.chunk_ids)}
                            for s in draft.sentences
                        ],
                    },
                ),
                usage_details={
                    "input_tokens": draft.prompt_tokens,
                    "output_tokens": draft.completion_tokens,
                },
                metadata={"prompt_version": draft.prompt_version},
            )
        return {"draft": draft}

    def check_citations(state: AskState) -> AskState:
        # Titles carry figures too (e.g. the agreement's date); both are shown to the model.
        citable = {c.id: f"{c.title}\n{c.text}" for c in state["search"].chunks}
        citable |= {r.ref: r.text() for r in state.get("rules", [])}
        with tracer.observe(
            "check-citations",
            "guardrail",
            input=text(
                tracer,
                [
                    {"text": s.text, "source_ids": list(s.chunk_ids)}
                    for s in state["draft"].sentences
                ],
            ),
        ) as obs:
            checked = check(state["draft"].sentences, citable)
            reason = abstain_reason(len(state["search"].chunks), state["draft"].declined, checked)
            obs.update(
                output=text(
                    tracer,
                    {
                        "removed": [
                            {"text": d.sentence.text, "reason": d.reason, "detail": d.detail}
                            for d in checked.dropped
                        ]
                    },
                ),
                metadata={
                    "kept_sentences": len(checked.kept),
                    "removed_sentences": len(checked.dropped),
                    "removed_reasons": [d.reason for d in checked.dropped],
                    "abstain_reason": reason,
                },
            )
        return {"checked": checked, "abstain_reason": reason or ""}

    def after_check(state: AskState) -> str:
        return "abstain" if state.get("abstain_reason") else "respond"

    def respond(state: AskState) -> AskState:
        found, checked = state["search"], state["checked"]
        by_id = {c.id: c for c in found.chunks}
        rules = {r.ref: r for r in state.get("rules", [])}
        numbers: dict[str, int] = {}
        sources: list[dict[str, Any]] = []
        sentences = []
        for sentence in checked.kept:
            refs = []
            for cited in sentence.chunk_ids:
                rule = rules.get(cited)
                ref = rule.source_ref if rule else by_id[cited].source_ref
                if ref not in numbers:
                    numbers[ref] = len(numbers) + 1
                    if rule:
                        sources.append(_rule_source(rule, numbers[ref], state["on"]))
                    else:
                        sources.append(_source(by_id[cited], numbers[ref], state["on"]))
                refs.append(numbers[ref])
            sentences.append({"text": sentence.text, "sources": sorted(set(refs))})
        extra = []
        if found.decision.governing == "agreement":
            extra.append("agreement_governs")
        if state["nationality"] == "unknown":
            extra.append("algerian_rules_differ")
        if state["scope"].personal:
            extra.append("personal")
        return {
            "response": {
                "kind": "answer",
                "lang": state["lang"],
                "sentences": sentences,
                "sources": sources,
                "governing": found.decision.governing,
                "notices": _notices(state, *extra),
            }
        }

    def refuse(state: AskState) -> AskState:
        return {"response": _fixed(state, "refuse", state["scope"].reason)}

    def handoff(state: AskState) -> AskState:
        return {"response": _fixed(state, "handoff", state["scope"].reason)}

    def abstain(state: AskState) -> AskState:
        response = _fixed(state, "abstain", None)
        found = state.get("search")
        if found and found.chunks:
            top = found.chunks[0]
            response["sources"] = [_source(top, 1, state["on"])]
        response["abstain_reason"] = state.get("abstain_reason") or "no_retrieval"
        return {"response": response}

    graph = StateGraph(AskState)
    graph.add_node("route", route)
    graph.add_node("clarify", clarify)
    graph.add_node("eligibility", eligibility)
    graph.add_node("retrieve", retrieve)
    graph.add_node("answer", answer)
    graph.add_node("check_citations", check_citations)
    graph.add_node("respond", respond)
    graph.add_node("refuse", refuse)
    graph.add_node("handoff", handoff)
    graph.add_node("abstain", abstain)
    graph.add_edge(START, "route")
    graph.add_conditional_edges("route", after_route, ["refuse", "handoff", "clarify"])
    graph.add_edge("clarify", "eligibility")
    graph.add_edge("eligibility", "retrieve")
    graph.add_conditional_edges("retrieve", after_retrieve, ["answer", "abstain"])
    graph.add_edge("answer", "check_citations")
    graph.add_conditional_edges("check_citations", after_check, ["respond", "abstain"])
    for terminal in ("respond", "refuse", "handoff", "abstain"):
        graph.add_edge(terminal, END)
    return graph.compile()


def _fixed(state: AskState, kind: Kind, topic: str | None) -> dict[str, Any]:
    return {
        "kind": kind,
        "lang": state["lang"],
        "sentences": [{"text": texts.response(kind, state["lang"], topic), "sources": []}],
        "sources": [],
        "governing": None,
        "notices": _notices(state),
        "reason": topic,
    }
