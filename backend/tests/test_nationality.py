import pytest

from guardrails.nationality import detect_nationality


@pytest.mark.parametrize(
    "question",
    [
        "Je suis étudiant algérien : quel titre de séjour ?",
        "Un Algérien peut-il obtenir le titre « talent » ?",
        "Étudiante algérienne, je cherche un emploi",
        "I am an Algerian student finishing my degree",
        "Where does a student renew a residence certificate?",
        "Comment renouveler mon certificat de résidence étudiant ?",
        "Je viens d'Algérie pour étudier",
    ],
)
def test_explicit_algerian_mentions_are_detected(question: str) -> None:
    assert detect_nationality(question) == "algerian"


@pytest.mark.parametrize(
    "question",
    [
        "Combien d'heures un étudiant étranger peut-il travailler ?",
        "Je suis marocain, quel titre après mon master ?",
        "What is the talent permit?",
        "Quelle carte de résident après 5 ans ?",
    ],
)
def test_other_questions_stay_unknown(question: str) -> None:
    assert detect_nationality(question) == "unknown"
