from datetime import UTC, datetime

from rag.answer import PROMPT_PATH, StructuredAnswer, render_prompt, to_draft
from rag.precedence import PrecedenceDecision
from rag.retrieval import RetrievedChunk

CHUNK = RetrievedChunk(
    "fact_sheet:F1:0",
    "fact_sheet",
    "F1",
    "Titre",
    "https://example.test/F1",
    "Texte fictif.",
    "all",
    "official",
    0.5,
    1,
    1,
    fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
)


def test_ids_are_cleaned_and_usage_is_read() -> None:
    structured = StructuredAnswer.model_validate(
        {
            "declined": False,
            "sentences": [{"text": "Une phrase.", "source_ids": [" [fact_sheet:F1:0] "]}],
        }
    )
    draft = to_draft(structured, {"input_tokens": 12, "output_tokens": 3})
    assert draft.sentences[0].chunk_ids == ("fact_sheet:F1:0",)
    assert (draft.prompt_tokens, draft.completion_tokens) == (12, 3)


def test_prompt_lists_sources_and_precedence_instruction() -> None:
    assert PROMPT_PATH.exists()
    agreement = render_prompt("Q ?", "en", [CHUNK], PrecedenceDecision("agreement", "rights"))
    assert "S1 Titre" in agreement and "Answer in English" in agreement
    assert "Franco-Algerian agreement" in agreement
    procedure = render_prompt("Q ?", "fr", [CHUNK], PrecedenceDecision("general", "procedure"))
    assert "about procedure" in procedure and "Answer in French" in procedure
    general = render_prompt("Q ?", "fr", [CHUNK], PrecedenceDecision("general", None))
    assert "Algerian" not in general


def test_source_labels_map_back_to_chunk_ids_and_unknown_labels_stay() -> None:
    structured = StructuredAnswer.model_validate(
        {"declined": False, "sentences": [{"text": "A.", "source_ids": ["S1", "[S1]", "S7"]}]}
    )
    assert to_draft(structured, None, [CHUNK]).sentences[0].chunk_ids == (
        "fact_sheet:F1:0",
        "fact_sheet:F1:0",
        "S7",
    )
