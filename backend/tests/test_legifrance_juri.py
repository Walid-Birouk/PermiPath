import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from ingestion.chunker import MAX_CHUNK_CHARS
from ingestion.drafts import SourceMeta
from ingestion.legifrance.juri import decision_drafts, decision_paragraphs, decision_url

FIXTURE = (
    Path(__file__).parent / "fixtures" / "legifrance" / "consult_juri_CETATEXT000053612496.json"
)
DECISION_ID = "CETATEXT000053612496"


@pytest.fixture
def decision() -> dict[str, Any]:
    text: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))["text"]
    return text


def test_decision_chunks_carry_identity_link_and_date(decision: dict[str, Any]) -> None:
    meta = SourceMeta(nationality_scope="algerian", journey_stages=("finishing",))
    drafts = decision_drafts(decision, meta)
    assert drafts, "the recorded decision has a text"
    assert {d.source_type for d in drafts} == {"case_law"}
    assert {d.source_id for d in drafts} == {DECISION_ID}
    assert [d.chunk_index for d in drafts] == list(range(len(drafts)))
    first = drafts[0]
    assert first.url == "https://www.legifrance.gouv.fr/ceta/id/CETATEXT000053612496"
    assert first.title.startswith(
        "Conseil d'État, 2ème - 7ème chambres réunies, 02/03/2026, 500835"
    )
    assert "ECLI:FR:CECHR:2026:500835.20260302" in first.title
    assert first.in_force_from == date(2026, 3, 2)
    assert first.meta == meta


def test_decision_text_is_kept_whole_and_split_on_paragraphs(decision: dict[str, Any]) -> None:
    drafts = decision_drafts(decision, SourceMeta())
    joined = " ".join(d.text for d in drafts)
    # Point 3 is the holding cited by golden items g020 and g022.
    assert "régit d'une manière complète" in joined
    assert "ne sont pas applicables aux ressortissants algériens" in joined
    assert all("<" not in d.text for d in drafts)
    paragraphs = decision_paragraphs(decision)
    assert len(paragraphs) > 1
    assert all(len(p) <= MAX_CHUNK_CHARS for p in paragraphs if p.startswith("3."))


def test_plain_text_is_used_when_html_is_missing(decision: dict[str, Any]) -> None:
    plain = {**decision, "texteHtml": None}
    assert decision_paragraphs(plain) == [decision["texte"].strip()]


def test_decision_url_depends_on_the_court() -> None:
    assert decision_url("JURITEXT000000000001").startswith("https://www.legifrance.gouv.fr/juri/")
    with pytest.raises(KeyError):
        decision_url("LEGIARTI000000000001")
