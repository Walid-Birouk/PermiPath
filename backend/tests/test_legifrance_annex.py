"""Row-by-row chunking of tabular CESEDA annexes. The fixture is a recorded PISTE sandbox
response for Annexe 10 (Légifrance / DILA, Licence Ouverte 2.0), trimmed to the header row and
points 1, 25 and 27 (see the fixture README)."""

import json
from datetime import date
from pathlib import Path

from ingestion.drafts import SourceMeta
from ingestion.legifrance.annex import annex_drafts, annex_rows
from ingestion.legifrance.client import ArticleVersion, _parse_article

FIXTURE = Path(__file__).parent / "fixtures" / "legifrance" / "consult_getArticle_annex10.json"
ON = date(2026, 11, 27)
ANNEX_CID = "LEGIARTI000043464443"


def _annex() -> ArticleVersion:
    return _parse_article(json.loads(FIXTURE.read_text(encoding="utf-8"))["article"])


def test_rows_skip_the_header_and_keep_columns() -> None:
    rows = annex_rows(_annex())
    assert [r.point for r in rows] == [1, 25, 27]
    student = rows[1]
    assert student.category == "Titre de séjour pour motif d'études"
    assert student.permit.startswith('CST portant la mention " étudiant "')
    assert student.references == "L. 422-1 L. 422-2 L. 422-5 L. 422-6"
    # Paragraph breaks of the source cell are kept as lines.
    assert student.body.splitlines()[0] == "1. Pièces à produire dans tous les cas :"


def test_drafts_cover_selected_points_and_report_missing_ones() -> None:
    meta = SourceMeta(journey_stages=("arriving", "renewing"))
    drafts, missing = annex_drafts(_annex(), "Annexe 10", {25: meta, 64: meta}, ON)
    assert missing == [64]
    assert {d.source_id for d in drafts} == {f"{ANNEX_CID}-point-25"}
    first = drafts[0]
    assert first.title == "CESEDA, annexe 10, point 25"
    assert first.source_type == "law"
    assert first.meta == meta
    assert first.in_force_from == date(2024, 7, 1)
    assert first.url.endswith("LEGIARTI000049820920/2026-11-27")
    assert first.text.startswith(
        "Annexe 10 > Point 25 : Titre de séjour pour motif d'études > CST portant la mention"
    )
    assert "Référence du CESEDA : L. 422-1 L. 422-2 L. 422-5 L. 422-6" in first.text


def test_long_rows_split_into_ordered_chunks_with_the_same_path() -> None:
    drafts, _ = annex_drafts(_annex(), "Annexe 10", {1: SourceMeta()}, ON, max_chars=1200)
    assert len(drafts) > 1
    assert [d.chunk_index for d in drafts] == list(range(len(drafts)))
    assert all(d.text.startswith("Annexe 10 > Point 1 : ") for d in drafts)
    assert all(len(d.text) <= 1200 + 200 for d in drafts)
