"""TOC resolution and article chunking. Fixtures are recorded PISTE sandbox responses
(Légifrance / DILA, Licence Ouverte 2.0); the TOC is trimmed (see the fixture README).
Synthetic article texts are placeholders, not legal content."""

import json
from datetime import date
from pathlib import Path
from typing import Any

from ingestion.chunker import MAX_CHUNK_CHARS
from ingestion.drafts import SourceMeta
from ingestion.legifrance.client import ArticleVersion, _parse_article
from ingestion.legifrance.loader import article_drafts
from ingestion.legifrance.toc import TocIndex, resolve

FIXTURES = Path(__file__).parent / "fixtures" / "legifrance"
STUDENT_SECTION = "LEGISCTA000042771652"
REPEALED_SECTION = "LEGISCTA000006147744"
L422_1_ID, L422_1_CID = "LEGIARTI000042776689", "LEGIARTI000042771654"
ON = date(2026, 11, 26)


def _json(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def _toc() -> TocIndex:
    return TocIndex(_json("consult_legi_tableMatieres.json"))


def test_section_resolves_to_in_force_articles_in_order() -> None:
    articles = _toc().section_articles(STUDENT_SECTION)
    assert articles is not None
    assert [a.num for a in articles] == ["L422-1", "L422-2", "L422-3"]
    assert articles[0].id == L422_1_ID
    assert articles[0].cid == L422_1_CID


def test_repealed_or_unknown_section_is_not_resolved() -> None:
    toc = _toc()
    assert toc.section_articles(REPEALED_SECTION) is None
    assert toc.section_articles("LEGISCTA000000000000") is None


def test_resolve_dedupes_and_reports_missing_sections() -> None:
    stale = "LEGIARTI000000000001"
    result = resolve(_toc(), [STUDENT_SECTION, "LEGISCTA000000000000"], [L422_1_ID, stale])
    assert len(result.article_ids) == 4
    assert result.article_ids[0] == L422_1_ID
    assert result.article_ids[-1] == stale
    assert result.unresolved_sections == ("LEGISCTA000000000000",)


def test_recorded_article_gives_one_chunk_keyed_by_cid() -> None:
    article = _parse_article(_json("consult_getArticle.json")["article"])
    assert article.cid == L422_1_CID
    assert article.section_path[-1] == "Section 1 : Etranger étudiant en France"
    assert article.nota.startswith("Conformément à l'article 20 de l'ordonnance")

    (draft,) = article_drafts(article, ON, SourceMeta(journey_stages=("arriving",)))
    assert draft.id == f"law:{L422_1_CID}:0"
    assert draft.title == "CESEDA, article L422-1"
    assert draft.url.endswith(f"/codes/article_lc/{L422_1_ID}/2026-11-26")
    assert draft.in_force_from == date(2021, 5, 1)
    assert draft.in_force_to is None
    first_line, body = draft.text.split("\n\n", 1)
    assert first_line.endswith("Section 1 : Etranger étudiant en France > Article L422-1")
    assert body.startswith("L'étranger qui établit")
    assert "Nota : Conformément" in draft.text


def test_long_article_is_split_and_keeps_its_id() -> None:
    article = ArticleVersion(
        id="LEGIARTI000000000002",
        num="R000-0",
        status="VIGUEUR",
        in_force_from=None,
        in_force_to=None,
        text="\n".join(f"Ligne de test {i}." * 20 for i in range(40)),
        cid="LEGIARTI000000000003",
    )
    drafts = article_drafts(article, ON, SourceMeta())
    assert len(drafts) > 1
    assert {d.source_id for d in drafts} == {"LEGIARTI000000000003"}
    assert [d.chunk_index for d in drafts] == list(range(len(drafts)))
    assert all(len(d.text) <= MAX_CHUNK_CHARS + 100 for d in drafts)
