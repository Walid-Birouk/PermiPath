"""Service-public sheet parser. Real sheets from the 3.5 feed (`tests/fixtures/service_public/`,
Service-Public.gouv.fr / DILA, Licence Ouverte 2.0) plus synthetic XML for rendering rules;
synthetic texts are placeholders, not legal content."""

from datetime import date
from pathlib import Path

import pytest

from ingestion.service_public.parser import (
    Sheet,
    SheetSection,
    parse_sheet_bytes,
    parse_sheet_file,
)

FIXTURES = Path(__file__).parent / "fixtures" / "service_public"
CESEDA_TEXT_ID = "LEGITEXT000006070158"


def _sheet(sheet_id: str) -> Sheet:
    return parse_sheet_file(FIXTURES / f"{sheet_id}.xml")


def _all_blocks(section: SheetSection) -> list[str]:
    return [*section.blocks, *(b for c in section.children for b in _all_blocks(c))]


def _synthetic(body: str) -> Sheet:
    xml = (
        '<Publication ID="F0" type="Fiche" spUrl="https://example.test/F0"'
        ' xmlns:dc="http://purl.org/dc/elements/1.1/">'
        "<dc:title>Titre</dc:title><dc:date>modified 2026-01-02</dc:date>"
        f"<Texte>{body}</Texte></Publication>"
    )
    return parse_sheet_bytes(xml.encode())


def test_sheet_metadata() -> None:
    sheet = _sheet("F2231")
    assert sheet.id == "F2231"
    assert sheet.title == "Étudiant étranger en France : visa de long séjour ou titre de séjour"
    assert sheet.url == "https://www.service-public.gouv.fr/particuliers/vosdroits/F2231"
    assert sheet.modified == date(2026, 8, 1)
    assert sheet.sheet_type == "Fiche d'information conditionnée"


def test_situations_become_top_level_sections() -> None:
    titles = [c.title for c in _sheet("F2231").root.children]
    assert titles == ["1ʳᵉ année", "Après 1 an de séjour"]


def test_texte_layout_sections() -> None:
    titles = [c.title for c in _sheet("F2733").root.children]
    assert titles[:3] == ["De quoi s'agit-il ?", "Qui est concerné ?", "Démarche"]


def test_introduction_is_root_text() -> None:
    assert _sheet("F2231").root.blocks
    assert _sheet("F31969").root.children[0].title is not None


def test_references_are_classified() -> None:
    refs = _sheet("F2231").references
    assert len(refs) == 9
    sections = [r for r in refs if r.kind == "section"]
    assert {r.text_id for r in sections} == {CESEDA_TEXT_ID}
    assert "LEGISCTA000042771652" in {r.legi_id for r in sections}
    assert [r.legi_id for r in refs if r.kind == "article"] == [
        "LEGIARTI000054301706",
        "LEGIARTI000043472320",
    ]
    assert [r.kind for r in refs].count("other") == 1


def test_online_service_is_rendered_with_url() -> None:
    blocks = _all_blocks(_sheet("F2231").root)
    assert (
        "Service en ligne : France-visas - Étudiants "
        "(https://france-visas.gouv.fr/web/france-visas/etudiant)"
    ) in blocks


def test_no_block_is_empty_or_has_raw_whitespace() -> None:
    for sheet_id in ("F2231", "F2733", "F31969"):
        for block in _all_blocks(_sheet(sheet_id).root):
            assert block.strip() == block
            assert block
            assert "  " not in block


def test_list_table_and_notes_rendering() -> None:
    sheet = _synthetic(
        "<Chapitre><Titre><Paragraphe>Chapitre A</Paragraphe></Titre>"
        "<Liste><Item><Paragraphe>un</Paragraphe></Item><Item><Paragraphe>deux</Paragraphe>"
        "</Item></Liste>"
        "<Tableau><TitreRiche><Paragraphe>Légende</Paragraphe></TitreRiche><Colonne/>"
        "<Rangée><Cellule><Paragraphe>a</Paragraphe><Paragraphe>b</Paragraphe></Cellule>"
        "<Cellule><Paragraphe>c</Paragraphe></Cellule></Rangée></Tableau>"
        "<ASavoir><Titre>À savoir</Titre><Paragraphe>note</Paragraphe></ASavoir>"
        "<Aide><Definition><Titre>Terme</Titre><Texte><Paragraphe>sens</Paragraphe></Texte>"
        "</Definition></Aide>"
        "<FragmentConditionne><Condition><estVrai var='T1'/></Condition>"
        "<Paragraphe>conditionnel</Paragraphe></FragmentConditionne>"
        "<TitreAlternatif><Paragraphe>doublon</Paragraphe></TitreAlternatif>"
        "<OuSAdresser><Titre>Préfecture</Titre><RessourceWeb URL='https://x.test'/>"
        "</OuSAdresser>"
        "</Chapitre>"
    )
    (chapter,) = sheet.root.children
    assert chapter.title == "Chapitre A"
    assert chapter.blocks == [
        "- un\n- deux",
        "Légende\na b | c",
        "À savoir : note",
        "Terme : sens",
        "conditionnel",
        "Où s'adresser : Préfecture",
    ]


def test_empty_sections_are_dropped() -> None:
    sheet = _synthetic("<Chapitre><Titre>Vide</Titre></Chapitre>")
    assert sheet.root.children == []


def test_rejects_non_publication() -> None:
    with pytest.raises(ValueError, match="Not a service-public publication"):
        parse_sheet_bytes(b"<Other/>")
