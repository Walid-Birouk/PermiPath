import pytest

from evals.golden_set import GOLDEN_DIR, load_golden_set
from guardrails.scope import classify

GOLDEN = load_golden_set(GOLDEN_DIR / "golden.yaml")
EXPECTED_ROUTE = {"refuse": "out_of_scope", "handoff": "handoff"}


@pytest.mark.parametrize("item", GOLDEN.items, ids=lambda i: i.id)
def test_golden_questions_are_routed_as_expected(item) -> None:  # type: ignore[no-untyped-def]
    assert classify(item.question).route == EXPECTED_ROUTE.get(item.expected_behaviour, "in_scope")


@pytest.mark.parametrize(
    ("question", "route", "reason"),
    [
        ("Puis-je faire une demande d'asile après mes études ?", "out_of_scope", "asylum"),
        ("Comment faire venir ma femme en France ?", "out_of_scope", "family_reunification"),
        ("How do I get French citizenship?", "out_of_scope", "naturalisation"),
        ("The prefecture rejected my renewal, can I appeal?", "handoff", "refusal_appeal"),
        ("J'ai une obligation de quitter le territoire", "handoff", "oqtf"),
        ("Mon titre est périmé depuis trois semaines", "handoff", "expired_permit"),
        ("Je suis sans-papiers, que faire ?", "handoff", "expired_permit"),
        ("Quels sont les recours possibles ?", "handoff", "refusal_appeal"),
    ],
)
def test_extra_cases(question: str, route: str, reason: str) -> None:
    decision = classify(question)
    assert (decision.route, decision.reason) == (route, reason)


@pytest.mark.parametrize(
    "question",
    [
        "Combien d'heures un étudiant étranger peut-il travailler ?",
        "When does my VLS-TS expire and how do I renew it?",
        "Quel est le délai pour demander la carte recherche d'emploi ?",
        "My student permit expires next month: how do I renew it?",
    ],
)
def test_ordinary_journey_questions_stay_in_scope(question: str) -> None:
    assert classify(question).route == "in_scope"


def test_personal_questions_are_flagged_but_still_answered() -> None:
    decision = classify("Dans mon cas, puis-je demander une carte pluriannuelle ?")
    assert decision.route == "in_scope" and decision.personal
    assert not classify("Quand demander une carte pluriannuelle ?").personal
