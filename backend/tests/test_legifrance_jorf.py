"""JORF treaty texts -> clause chunks, on recorded PISTE sandbox responses for the 2nd and 3rd
amendments to the Franco-Algerian agreement (Légifrance / DILA, Licence Ouverte 2.0)."""

import json
import re
from pathlib import Path
from typing import Any

import pytest

from ingestion.drafts import SourceMeta
from ingestion.legifrance.client import _html_to_text
from ingestion.legifrance.jorf import PREAMBLE, jorf_clauses, jorf_drafts

FIXTURES = Path(__file__).parent / "fixtures" / "legifrance"
THIRD, SECOND = "JORFTEXT000000599731", "JORFTEXT000000551016"


def _text(cid: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / f"consult_jorf_{cid}.json").read_text(encoding="utf-8")
    )
    return data


def test_decree_articles_come_first_then_annex_clauses_in_order() -> None:
    paths = [c.path for c in jorf_clauses(_text(THIRD))]
    assert paths[:3] == [("Décret, article 1",), ("Décret, article 2",), ("Annexe", PREAMBLE)]
    assert [p[1] for p in paths[3:]] == ["Article 1er", *(f"Article {n}" for n in range(2, 13))]


def test_new_article_headings_are_kept() -> None:
    headings = [c.path[-1] for c in jorf_clauses(_text(SECOND))]
    assert "Article 8 (nouveau)" in headings
    assert "Article 6" in headings


@pytest.mark.parametrize("cid", [THIRD, SECOND])
def test_no_text_is_lost(cid: str) -> None:
    def words(text: str) -> list[str]:
        return re.findall(r"\w+", text)

    source = [w for a in _text(cid)["articles"] for w in words(_html_to_text(a["content"] or ""))]
    clauses = jorf_clauses(_text(cid))
    headings = [" ".join(p for p in c.path[1:] if p != PREAMBLE) for c in clauses]
    produced = [
        w for c, h in zip(clauses, headings, strict=True) for w in (*words(h), *words(c.text))
    ]
    assert sorted(produced) == sorted(source)


def test_drafts_are_treaty_chunks_keyed_by_text_cid() -> None:
    meta = SourceMeta(nationality_scope="algerian")
    drafts = jorf_drafts(_text(THIRD), meta)
    assert {d.source_type for d in drafts} == {"treaty"}
    assert {d.source_id for d in drafts} == {THIRD}
    assert [d.chunk_index for d in drafts] == list(range(len(drafts)))
    assert drafts[0].url == f"https://www.legifrance.gouv.fr/jorf/id/{THIRD}"
    assert drafts[0].title.startswith("Décret n° 2002-1500 du 20 décembre 2002")
    assert all(d.meta.nationality_scope == "algerian" for d in drafts)
    assert drafts[3].text.startswith("Annexe > Article 1er\n\n")
