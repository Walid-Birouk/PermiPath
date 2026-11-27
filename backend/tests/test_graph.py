"""One test per path of the agent graph, with a fake retriever and a fake model.

Chunk texts and figures are made up (CLAUDE.md, rule 6)."""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

import pytest

from guardrails import texts
from rag.answer import DraftAnswer
from rag.citations import AnswerSentence
from rag.graph import Deps, build_graph
from rag.precedence import PrecedenceDecision
from rag.retrieval import Filters, RetrievalResult, RetrievedChunk
from rag.rules import RuleRow

ON = date(2026, 11, 27)


def chunk(
    chunk_id: str, text: str, source_type: str = "fact_sheet", scope: str = "all"
) -> RetrievedChunk:
    source_id = chunk_id.split(":")[1]
    return RetrievedChunk(
        id=chunk_id,
        source_type=source_type,
        source_id=source_id,
        title=f"Titre {source_id}",
        url=f"https://example.test/{source_id}",
        text=text,
        nationality_scope=scope,
        authority="official",
        score=0.5,
        lexical_rank=1,
        vector_rank=1,
        fetched_at=datetime(2026, 11, 20, tzinfo=UTC),
    )


SHEET = chunk("fact_sheet:F1:0", "La carte fictive est valable 7 lunes.")
SHEET_2 = chunk("fact_sheet:F2:0", "La demande fictive se fait en ligne.")
AGREEMENT = chunk(
    "treaty:accord:article-x", "Le certificat fictif dure 3 saisons.", "treaty", "algerian"
)


@dataclass
class FakeRetriever:
    chunks: list[RetrievedChunk]
    calls: list[Filters] = field(default_factory=list)

    def retrieve(
        self,
        question: str,
        filters: Filters,
        depth: int = 10,
        query_vector: Sequence[float] | None = None,
    ) -> RetrievalResult:
        self.calls.append(filters)
        found = [
            c
            for c in self.chunks
            if c.nationality_scope in filters.nationality_scopes
            and (filters.source_types is None or c.source_type in filters.source_types)
        ]
        return RetrievalResult(found[:depth], 5, len(found), len(found), [0.1])


@dataclass
class FakeAnswerer:
    draft: DraftAnswer
    seen: list[PrecedenceDecision] = field(default_factory=list)
    seen_chunks: list[list[str]] = field(default_factory=list)
    seen_rules: list[list[str]] = field(default_factory=list)

    def answer(
        self,
        question: str,
        lang: Any,
        chunks: Sequence[RetrievedChunk],
        decision: PrecedenceDecision,
        rules: Sequence[RuleRow] = (),
    ) -> DraftAnswer:
        self.seen.append(decision)
        self.seen_chunks.append([c.id for c in chunks])
        self.seen_rules.append([r.id for r in rules])
        return self.draft


@dataclass
class FakeRules:
    rows: list[RuleRow]
    calls: list[tuple[str, bool]] = field(default_factory=list)

    def lookup(self, question: str, algerian: bool) -> list[RuleRow]:
        self.calls.append((question, algerian))
        return [r for r in self.rows if (r.nationality_scope == "algerian") == algerian]


RULE = RuleRow(
    id="fictive-hours",
    permit="cst_etudiant",
    nationality_scope="all",
    topic="work_limit",
    label_fr="Plafond fictif",
    label_en="Fictive cap",
    value="321 heures par an",
    quote="Le plafond fictif est de 321 heures par an.",
    source_type="fact_sheet",
    source_id="F1",
    source_title="Titre F1",
    url="https://example.test/F1",
    source_checked_at=datetime(2026, 11, 20, tzinfo=UTC),
    verified_at=date(2026, 11, 27),
)


def run(
    question: str,
    chunks: list[RetrievedChunk],
    draft: DraftAnswer,
    rules: FakeRules | None = None,
    **request: Any,
) -> dict[str, Any]:
    answerer = FakeAnswerer(draft)
    graph = build_graph(Deps(FakeRetriever(chunks), answerer, rules))
    state = graph.invoke({"question": question, "on": ON, **request})
    response: dict[str, Any] = state["response"]
    response["_answerer"] = answerer
    return response


SUPPORTED = DraftAnswer(
    declined=False,
    sentences=[
        AnswerSentence("La carte est valable 7 lunes.", ("fact_sheet:F1:0",)),
        AnswerSentence("La demande se fait en ligne.", ("fact_sheet:F2:0", "fact_sheet:F1:0")),
    ],
)
NOT_LEGAL_ADVICE_FR = texts.notice("not_legal_advice", "fr")


def test_answer_path_numbers_sources_and_keeps_notices() -> None:
    response = run("Quelle est la durée de la carte étudiant ?", [SHEET, SHEET_2], SUPPORTED)
    assert response["kind"] == "answer"
    assert response["lang"] == "fr"
    assert [s["sources"] for s in response["sentences"]] == [[1], [1, 2]]
    assert [s["ref"] for s in response["sources"]] == ["fact_sheet:F1", "fact_sheet:F2"]
    assert response["sources"][0]["in_force_on"] == "2026-11-27"
    assert response["sources"][0]["checked_at"] == "2026-11-20"
    assert response["notices"][0] == NOT_LEGAL_ADVICE_FR
    # Nationality unknown: the user is told Algerian rules differ.
    assert texts.notice("algerian_rules_differ", "fr") in response["notices"]


def test_unsupported_sentences_are_removed_and_the_rest_is_answered() -> None:
    draft = DraftAnswer(
        False,
        [
            AnswerSentence("La carte est valable 7 lunes.", ("fact_sheet:F1:0",)),
            AnswerSentence("Elle coûte 99 pièces.", ("fact_sheet:F1:0",)),
        ],
    )
    response = run("Durée de la carte ?", [SHEET], draft)
    assert [s["text"] for s in response["sentences"]] == ["La carte est valable 7 lunes."]


@pytest.mark.parametrize(
    ("question", "kind", "reason"),
    [
        ("Comment demander l'asile ?", "refuse", "asylum"),
        ("J'ai reçu une OQTF, que faire ?", "handoff", "oqtf"),
    ],
)
def test_refuse_and_handoff_paths_never_retrieve(question: str, kind: str, reason: str) -> None:
    answerer = FakeAnswerer(SUPPORTED)
    retriever = FakeRetriever([SHEET])
    state = build_graph(Deps(retriever, answerer)).invoke({"question": question, "on": ON})
    response = state["response"]
    assert (response["kind"], response["reason"]) == (kind, reason)
    assert response["notices"] == [NOT_LEGAL_ADVICE_FR]
    assert retriever.calls == [] and answerer.seen == []


@pytest.mark.parametrize(
    ("chunks", "draft", "reason"),
    [
        ([], SUPPORTED, "no_retrieval"),
        ([SHEET], DraftAnswer(declined=True, sentences=[]), "model_declined"),
        (
            [SHEET],
            DraftAnswer(False, [AnswerSentence("Elle coûte 99 pièces.", ("fact_sheet:F1:0",))]),
            "no_supported_sentence",
        ),
        (
            [SHEET],
            DraftAnswer(False, [AnswerSentence("Une source inventée.", ("fact_sheet:F9:0",))]),
            "no_supported_sentence",
        ),
    ],
)
def test_abstain_paths(chunks: list[RetrievedChunk], draft: DraftAnswer, reason: str) -> None:
    response = run("Quelle banque choisir ?", chunks, draft)
    assert response["kind"] == "abstain"
    assert response["abstain_reason"] == reason
    assert response["sentences"][0]["text"] == texts.response("abstain", "fr")
    # The official page to consult is the best retrieved source, when there is one.
    assert [s["ref"] for s in response["sources"]] == (["fact_sheet:F1"] if chunks else [])


def test_algerian_rights_question_puts_the_agreement_first() -> None:
    draft = DraftAnswer(
        False, [AnswerSentence("Le certificat dure 3 saisons.", ("treaty:accord:article-x",))]
    )
    response = run("Un étudiant algérien peut-il travailler ?", [SHEET, AGREEMENT], draft)
    answerer = response["_answerer"]
    assert answerer.seen[0].governing == "agreement"
    assert answerer.seen_chunks[0][0] == "treaty:accord:article-x"
    assert response["governing"] == "agreement"
    assert texts.notice("agreement_governs", "fr") in response["notices"]
    assert texts.notice("algerian_rules_differ", "fr") not in response["notices"]


def test_algerian_sources_are_excluded_for_other_nationalities() -> None:
    response = run("Quelle est la durée ?", [AGREEMENT, SHEET], SUPPORTED, nationality="other")
    assert "treaty:accord:article-x" not in response["_answerer"].seen_chunks[0]
    assert texts.notice("algerian_rules_differ", "fr") not in response["notices"]


def test_personal_question_and_english_detection() -> None:
    response = run(
        "In my case, which permit should I ask for after my master?", [SHEET, SHEET_2], SUPPORTED
    )
    assert response["lang"] == "en"
    assert response["notices"][0] == texts.notice("not_legal_advice", "en")
    assert texts.notice("personal", "en") in response["notices"]


def test_eligibility_rules_are_passed_cited_and_shown_as_their_source() -> None:
    draft = DraftAnswer(
        False,
        [
            AnswerSentence("Le plafond est de 321 heures par an.", ("rule:fictive-hours",)),
            AnswerSentence("La carte est valable 7 lunes.", ("fact_sheet:F1:0",)),
        ],
    )
    rules = FakeRules([RULE])
    response = run("Combien d'heures puis-je travailler ?", [SHEET], draft, rules)
    assert rules.calls == [("Combien d'heures puis-je travailler ?", False)]
    assert response["_answerer"].seen_rules == [["fictive-hours"]]
    assert [s["sources"] for s in response["sentences"]] == [[1], [1]]
    # The rule and the chunk quote the same sheet: one source, marked with the rule.
    assert len(response["sources"]) == 1
    assert response["sources"][0]["ref"] == "fact_sheet:F1"
    assert response["sources"][0]["rule"] == "fictive-hours"


def test_a_rule_figure_must_be_in_the_rule() -> None:
    draft = DraftAnswer(
        False, [AnswerSentence("Le plafond est de 999 heures.", ("rule:fictive-hours",))]
    )
    response = run("Combien d'heures ?", [SHEET], draft, FakeRules([RULE]))
    assert response["kind"] == "abstain"


def test_algerian_questions_get_algerian_rules_only() -> None:
    algerian_rule = RuleRow(**{**RULE.__dict__, "id": "alg", "nationality_scope": "algerian"})
    rules = FakeRules([RULE, algerian_rule])
    run("Un étudiant algérien peut-il travailler ?", [AGREEMENT], SUPPORTED, rules)
    assert rules.calls[0][1] is True
