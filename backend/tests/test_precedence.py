from dataclasses import dataclass

import pytest

from rag.precedence import decide, merge, order, topic_of


@dataclass(frozen=True)
class C:
    name: str
    source_type: str
    nationality_scope: str = "all"

    @property
    def id(self) -> str:
        return self.name


CHUNKS = [
    C("sheet", "fact_sheet"),
    C("ceseda", "law"),
    C("alg-sheet", "fact_sheet", "algerian"),
    C("agreement", "treaty", "algerian"),
    C("ruling", "case_law", "algerian"),
]


@pytest.mark.parametrize(
    ("question", "topic"),
    [
        ("Un étudiant algérien peut-il travailler pendant ses études ?", "rights"),
        ("Quel certificat de résidence demander avec une offre d'emploi ?", "rights"),
        ("Comment un Algérien obtient-il un certificat de 10 ans ?", "rights"),
        ("Where does an Algerian student apply to renew (ANEF or prefecture)?", "procedure"),
        ("Quels documents pour renouveler mon certificat de résidence ?", "procedure"),
        ("Quel est le coût du certificat de résidence étudiant ?", "procedure"),
    ],
)
def test_topic(question: str, topic: str) -> None:
    assert topic_of(question) == topic


def test_rights_question_puts_agreement_and_case_law_first() -> None:
    decision = decide("Un Algérien peut-il obtenir le titre talent ?", algerian=True)
    assert decision.governing == "agreement"
    assert [c.name for c in order(CHUNKS, decision)] == [
        "agreement",
        "ruling",
        "alg-sheet",
        "sheet",
        "ceseda",
    ]


def test_procedure_question_puts_sheets_first_and_keeps_the_agreement() -> None:
    decision = decide("Où déposer la demande de renouvellement ?", algerian=True)
    assert decision.governing == "general"
    assert [c.name for c in order(CHUNKS, decision)] == [
        "alg-sheet",
        "sheet",
        "ceseda",
        "agreement",
        "ruling",
    ]


def test_non_algerian_question_keeps_retrieval_order() -> None:
    decision = decide("Un étudiant peut-il travailler ?", algerian=False)
    assert decision.governing == "general" and decision.topic is None
    assert order(CHUNKS, decision) == CHUNKS


def test_merge_appends_only_new_chunks() -> None:
    extra = [C("agreement", "treaty", "algerian"), C("other-article", "treaty", "algerian")]
    merged = merge(CHUNKS[:2], [*extra, CHUNKS[0]])
    assert [c.name for c in merged] == ["sheet", "ceseda", "agreement", "other-article"]
