"""Chunk texts and figures are made up for the test (CLAUDE.md, rule 6)."""

import pytest

from rag.abstain import abstain_reason
from rag.citations import AnswerSentence, CheckedAnswer, check, figures

CHUNKS = {
    "c1": "Le plafond fictif est de 12 345,5 unités par an, soit 77 % du total.",
    "c2": "La démarche fictive se fait en ligne dans un délai de 9 jours.",
}


def S(text: str, *ids: str) -> AnswerSentence:
    return AnswerSentence(text, ids)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12 345,5 unités", {"12345.5"}),
        ("12345.50", {"12345.5"}),
        ("77 % et 9 jours", {"77", "9"}),
        ("article L. 422-1", {"422", "1"}),
        ("aucun chiffre", set()),
        ("09 jours", {"9"}),
        ("39" + chr(0x00A0) + "582 € et 2" + chr(0x202F) + "800,53 €", {"39582", "2800.53"}),
    ],
)
def test_figures_are_normalised(text: str, expected: set[str]) -> None:
    assert figures(text) == expected


def test_supported_sentences_are_kept_in_order() -> None:
    result = check(
        [S("Le plafond est de 12345,5 unités.", "c1"), S("En ligne, sous 9 jours.", "c2")], CHUNKS
    )
    assert [s.chunk_ids for s in result.kept] == [("c1",), ("c2",)]
    assert result.dropped == []
    assert result.cited_chunk_ids == ["c1", "c2"]


@pytest.mark.parametrize(
    ("sentence", "reason"),
    [
        (S("Une phrase sans source."), "no_citation"),
        (S("Une source inventée.", "c9"), "unknown_chunk"),
        (S("Le plafond est de 15 000 unités.", "c1"), "figure_not_in_source"),
        # The figure exists, but in a chunk the sentence does not cite.
        (S("Le délai est de 9 jours.", "c1"), "figure_not_in_source"),
    ],
)
def test_unsupported_sentences_are_dropped_with_a_reason(
    sentence: AnswerSentence, reason: str
) -> None:
    result = check([sentence], CHUNKS)
    assert result.kept == []
    assert result.dropped[0].reason == reason


def test_abstain_gate() -> None:
    supported = check([S("En ligne.", "c2")], CHUNKS)
    unsupported = check([S("Le plafond est de 1 unité.", "c2")], CHUNKS)
    assert abstain_reason(0, False, None) == "no_retrieval"
    assert abstain_reason(3, True, supported) == "model_declined"
    assert abstain_reason(3, False, unsupported) == "no_supported_sentence"
    assert abstain_reason(3, False, CheckedAnswer([], [])) == "no_supported_sentence"
    assert abstain_reason(3, False, supported) is None
