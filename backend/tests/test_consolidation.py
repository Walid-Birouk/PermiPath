"""Consolidated Franco-Algerian agreement (`sources/franco-algerian-agreement.yaml`): schema,
chunking, and cross-checks of the wording against the recorded JORF texts of the second and
third amendments (Légifrance / DILA, Licence Ouverte 2.0)."""

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from ingestion.consolidation import Consolidation, consolidation_drafts, load_consolidation
from ingestion.drafts import SourceMeta
from ingestion.legifrance.jorf import jorf_clauses
from ingestion.manifest import MANIFEST_PATH, load_manifest

FIXTURES = Path(__file__).parent / "fixtures" / "legifrance"
SECOND, THIRD = "avenant-1994", "avenant-2001"
JORF = {SECOND: "JORFTEXT000000551016", THIRD: "JORFTEXT000000599731"}
SOURCE_ID = "accord-franco-algerien-1968"


def _repo_consolidation() -> Consolidation:
    entry = load_manifest().treaties.consolidations[0]
    return load_consolidation(MANIFEST_PATH.parent / entry.path)


def _verified() -> Consolidation:
    return _repo_consolidation().model_copy(
        update={"status": "verified", "verified_by": "test", "verified_at": date(2026, 1, 1)}
    )


_UNACCENTED_CAPITALS = str.maketrans("ÉÈÊÀÂÎÔÛÇ", "EEEAAIOUC")
"""Older JO issues print capitals without accents (e.g. "Evian")."""


_FIGURES = {
    "vingt et un": "21",
    "dix-huit": "18",
    "quinze": "15",
    "seize": "16",
    "dix": "10",
    "huit": "8",
    "six": "6",
    "cinq": "5",
    "trois": "3",
    "deux": "2",
}
_UNIT = (
    r"(?=(?: et \w[\w -]*?)?\s(?:ans|an|mois|années|année|jours)\b"
    r"|\s(?:à|et) (?:dix-huit|vingt et un|18|21) ans)"
)


def _digits(text: str) -> str:
    """Figures in digits, as written in the consolidation (see its `editorial_notes`)."""
    text = text.replace("20 p. 100", "20 %")
    for word, number in _FIGURES.items():
        text = re.sub(rf"\b{word}\b{_UNIT}", number, text)
    return text


def _normalise(text: str) -> str:
    """Quotation marks and the spaces around them removed: the Légifrance text of the third
    amendment drops closing quotation marks."""
    spaced = " ".join(re.sub(r'[«»"]', " ", _digits(text).translate(_UNACCENTED_CAPITALS)).split())
    return re.sub(r" ([,;.:])", r"\1", spaced)


def _jorf_text(instrument: str) -> str:
    data: dict[str, Any] = json.loads(
        (FIXTURES / f"consult_jorf_{JORF[instrument]}.json").read_text(encoding="utf-8")
    )
    return _normalise("\n".join(c.text for c in jorf_clauses(data)))


def _provision_text(provision_id: str) -> str:
    return next(p.text for p in _repo_consolidation().provisions if p.id == provision_id)


def test_repository_consolidation_is_valid_and_not_yet_verified() -> None:
    consolidation = _repo_consolidation()
    assert consolidation.status == "draft"
    repealed = {p.id for p in consolidation.provisions if p.status == "repealed"}
    assert repealed == {"article-1", "article-2", "annexe"}


def test_replaced_or_inserted_wording_matches_the_amending_text() -> None:
    """Provisions whose wording was last set as a whole by the 1994 or 2001 amendment are
    reproduced alinéa by alinéa from the recorded JORF text."""
    checked = 0
    for provision in _repo_consolidation().provisions:
        last = provision.history[-1]
        if last.instrument not in JORF or last.change not in ("replaced", "inserted"):
            continue
        official = _jorf_text(last.instrument)
        for line in provision.text.splitlines():
            assert _normalise(line) in official, (provision.id, line)
        checked += 1
    # Articles 4, 5, 6 and 7 ter (third amendment); 8, 10, 12 and protocol title IV (second).
    assert checked == 8


@pytest.mark.parametrize(
    ("instrument", "provision_id", "phrase"),
    [
        (THIRD, "article-7", "l'article 6 nouveau"),
        (THIRD, "article-7", "après le contrôle médical d'usage"),
        (THIRD, "article-7", "ministre chargé de l'emploi"),
        (THIRD, "article-7", "vie privée et familiale"),
        (
            THIRD,
            "article-7-bis",
            "sous réserve de la régularité du séjour pour ce qui "
            "concerne les catégories visées au a, au b, au c, et au g",
        ),
        (THIRD, "article-7-bis", "ou de maladie professionnelle"),
        (THIRD, "article-7-bis", "au titre du regroupement familial"),
        (
            THIRD,
            "article-7-bis",
            "Les certificats de résidence valables dix ans sont délivrés "
            "et renouvelés gratuitement.",
        ),
        (THIRD, "article-9", "(lettres c et d)"),
        (THIRD, "protocole-titre-1", "d'un document de voyage en cours de validité"),
        (THIRD, "protocole-titre-2", "dans l'intérêt supérieur de l'enfant"),
        (THIRD, "protocole-titre-3", "conformément à l'article 7 e de l'accord"),
    ],
)
def test_amended_phrases_come_from_the_amendment(
    instrument: str, provision_id: str, phrase: str
) -> None:
    assert _normalise(phrase) in _jorf_text(instrument)
    assert _normalise(phrase) in _normalise(_provision_text(provision_id))


@pytest.mark.parametrize(
    ("provision_id", "removed"),
    [
        ("article-3", "semestriellement"),
        ("article-7", "travailleurs immigrés"),
        ("article-7", "membre de famille"),
        ("article-7-bis", "depuis plus de 15 ans"),
        ("article-9", "(lettres a à d)"),
        ("protocole-titre-1", "carte nationale d'identité"),
        ("protocole-titre-3", "travailleurs immigrés"),
        ("protocole-titre-3", "Le contrat de travail visé constitue"),
    ],
)
def test_replaced_wording_is_gone(provision_id: str, removed: str) -> None:
    assert removed not in _provision_text(provision_id)


def test_drafts_cover_provisions_in_force_with_their_last_instrument() -> None:
    meta = SourceMeta(nationality_scope="algerian")
    drafts = consolidation_drafts(_verified(), SOURCE_ID, meta)
    by_source = {d.source_id: d for d in drafts if d.chunk_index == 0}
    assert f"{SOURCE_ID}:article-1" not in by_source
    article = by_source[f"{SOURCE_ID}:article-7-bis"]
    assert article.title == "Accord franco-algérien du 27 décembre 1968, article 7 bis"
    assert article.url.endswith(JORF[THIRD])
    assert article.source_type == "treaty"
    assert article.meta.nationality_scope == "algerian"
    assert article.meta.journey_stages == ("settling",)
    assert article.in_force_from == date(2003, 1, 1)
    eight = by_source[f"{SOURCE_ID}:article-8"]
    assert eight.in_force_from == date(1994, 9, 28)
    title = by_source[f"{SOURCE_ID}:protocole-titre-3"].title
    assert title.endswith("protocole, titre III")
    last_chunk = [d for d in drafts if d.source_id == f"{SOURCE_ID}:article-7-bis"][-1]
    assert "Rédaction issue de : " in last_chunk.text


def _raw() -> dict[str, Any]:
    return _repo_consolidation().model_dump(mode="json")


def test_repealed_provision_cannot_carry_text() -> None:
    data = _raw()
    article = next(p for p in data["provisions"] if p["id"] == "article-1")
    article["text"] = "Texte de test."
    with pytest.raises(ValidationError, match="text is required"):
        Consolidation.model_validate(data)


def test_history_must_reference_known_instruments() -> None:
    data = _raw()
    data["provisions"][0]["history"][0]["instrument"] = "avenant-inconnu"
    with pytest.raises(ValidationError, match="unknown instruments"):
        Consolidation.model_validate(data)


def test_verified_status_requires_a_reviewer() -> None:
    data = _raw()
    data["status"] = "verified"
    with pytest.raises(ValidationError, match="verified_by"):
        Consolidation.model_validate(data)


# --- 1968 and 1985 wording against the JO scans -------------------------------------------

JO_SCANS = {
    "accord-1968": "jo_scan_JORF_19690322_69_p5-7.txt",
    "avenant-1985": "jo_scan_JORF_19860308_57_p14-16.txt",
}
THIRD_AMENDMENT_WORDS = (
    "l'article 6 nouveau",
    "nouveau",
    "après le contrôle médical d'usage",
    "ministre chargé de l'emploi",
    "de plein droit",
    "vie privée et familiale",
    "sous réserve de la régularité du séjour pour ce qui concerne les catégories visées au a, "
    "au b, au c, et au g",
    "ou de maladie professionnelle",
    "ainsi qu'aux ayants droit d'un ressortissant algérien, bénéficiaires d'une rente de décès "
    "pour accident de travail ou maladie professionnelle versée par un organisme français",
    "au titre du regroupement familial",
    "d'un document de voyage en cours de validité",
    ", dans l'intérêt supérieur de l'enfant",
    "portant la mention travailleur temporaire conformément à l'article 7 e de l'accord",
)


def _scan_text(name: str) -> str:
    raw = (FIXTURES / name).read_text(encoding="utf-8")
    joined = re.sub("\u00ad\\s*", "", raw).replace("\u2019", "'").replace("l*r", "1er")
    return _normalise(joined)


@pytest.mark.parametrize(
    "provision_id",
    [
        "preambule",
        "article-3",
        "article-11",
        "article-7",
        "article-7-bis",
        "protocole-titre-1",
        "protocole-titre-2",
        "protocole-titre-3",
    ],
)
def test_1968_and_1985_wording_matches_the_jo_scans(provision_id: str) -> None:
    """Outside the words set by the third amendment (and the 1985 change to article 3),
    every alinéa is found in the text layer of the JO issue that published it."""
    provision = next(p for p in _repo_consolidation().provisions if p.id == provision_id)
    wording = [c for c in provision.history if c.change in ("original", "inserted", "replaced")]
    source = wording[-1].instrument
    scan = _scan_text(JO_SCANS[source])
    third = _jorf_text(THIRD)
    pattern = "|".join(re.escape(_normalise(w)) for w in (*THIRD_AMENDMENT_WORDS, "annuellement"))
    for line in provision.text.splitlines():
        text = _normalise(line)
        if text in third:
            continue  # alinéa written entirely by the third amendment
        for segment in re.split(pattern, text):
            segment = segment.strip(" ,;.:")
            if len(segment) >= 12:
                assert segment in scan, (provision_id, segment)


THIRD_AMENDMENT_SCAN = "jo_scan_JORF_20021226_300_p14.txt"


def test_third_amendment_in_force_date_matches_the_jo_footnote() -> None:
    """Footnote (1) to the title of Décret n° 2002-1500 (JO of 26 December 2002) states the
    entry into force; the Légifrance API text omits it. The text layer reads "1er" as "1~"."""
    raw = (FIXTURES / THIRD_AMENDMENT_SCAN).read_text(encoding="utf-8")
    assert "Décret n° 2002-1500" in raw
    footnote = r"\(1\) Le présent avenant entrera en vigueur le 1\S{0,2} janvier (\d{4})"
    found = re.search(footnote, raw)
    assert found is not None
    third = next(i for i in _repo_consolidation().instruments if i.id == THIRD)
    assert third.in_force_from == date(int(found.group(1)), 1, 1)
