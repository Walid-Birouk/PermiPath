"""Parser for Service-Public.gouv.fr "vosdroits" sheets (XML feed, schema 3.5).

The feed is published by DILA under Licence Ouverte 2.0:
https://lecomarquage.service-public.gouv.fr/vdd/3.5/part/zip/vosdroits-latest.zip
Schema: https://echanges.dila.gouv.fr/OPENDATA/SERVICE-PUBLIC_DTD/schema_3.5.zip

A sheet is parsed into a tree of titled sections (`SheetSection`) whose text is rendered as
plain text, plus the legal references the sheet cites. Sizing (chunking) is not done here.
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Literal

DC = "{http://purl.org/dc/elements/1.1/}"

# Containers whose children are processed as if they belonged to the parent.
_WRAPPERS = frozenset(
    {"Texte", "ListeSituations", "BlocCas", "Introduction", "FragmentConditionne", "Aide"}
)
# Containers that open a titled sub-section.
_SECTIONS = frozenset({"Situation", "Chapitre", "SousChapitre", "Cas"})
# Elements that carry no body text.
_SKIPPED = frozenset(
    {"Titre", "TitreRiche", "TitreAlternatif", "Condition", "Colonne", "RessourceWeb", "Source"}
)
# Links to an official online service or page, rendered as "<label> : <title> (<url>)".
_LINKS = {"ServiceEnLigne": "Service en ligne", "PourEnSavoirPlus": "Pour en savoir plus"}
# Titled notes rendered as "<title> : <text>".
_NOTES = frozenset({"ASavoir", "Attention", "ANoter", "Rappel", "Exemple"})

_WHITESPACE = re.compile(r"\s+")
_ARTICLE_URL = re.compile(r"/codes/article_lc/(LEGIARTI\d+)")
_SECTION_URL = re.compile(r"/codes/section_lc/(LEGITEXT\d+)/(LEGISCTA\d+)")
_JORF_URL = re.compile(r"(JORFTEXT\d+)")
_MODIFIED = re.compile(r"modified\s+(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class LegalReference:
    """A `Reference` element: the legal basis cited by the sheet."""

    kind: Literal["article", "section", "jorf_text", "other"]
    url: str
    title: str
    legi_id: str | None = None
    """LEGIARTI id (article), LEGISCTA id (section) or JORFTEXT id (jorf_text)."""
    text_id: str | None = None
    """LEGITEXT id of the code, for sections."""


@dataclass
class SheetSection:
    title: str | None
    blocks: list[str] = field(default_factory=list)
    children: list["SheetSection"] = field(default_factory=list)


@dataclass(frozen=True)
class Sheet:
    id: str
    title: str
    url: str
    sheet_type: str
    modified: date | None
    root: SheetSection
    references: tuple[LegalReference, ...]


def parse_sheet_file(path: Path) -> Sheet:
    return parse_sheet(ET.parse(path).getroot())


def parse_sheet_bytes(data: bytes) -> Sheet:
    return parse_sheet(ET.fromstring(data))


def parse_sheet(root: ET.Element) -> Sheet:
    if root.tag != "Publication":
        raise ValueError(f"Not a service-public publication: <{root.tag}>")
    sheet_id = root.attrib["ID"]
    body = SheetSection(title=None)
    for child in root:
        if child.tag in ("Introduction", "Texte", "ListeSituations"):
            _fill(body, child)
    return Sheet(
        id=sheet_id,
        title=_text(root.find(f"{DC}title")),
        url=root.attrib.get("spUrl", ""),
        sheet_type=root.attrib.get("type", ""),
        modified=_modified(root),
        root=body,
        references=tuple(_reference(r) for r in root.findall("Reference")),
    )


def _fill(section: SheetSection, element: ET.Element) -> None:
    for child in element:
        if child.tag in _WRAPPERS:
            _fill(section, child)
        elif child.tag in _SECTIONS:
            sub = SheetSection(title=_text(child.find("Titre")) or None)
            _fill(sub, child)
            if sub.blocks or sub.children:
                section.children.append(sub)
        elif child.tag not in _SKIPPED and (block := _render_block(child)):
            section.blocks.append(block)


def _render_block(element: ET.Element) -> str:
    tag = element.tag
    if tag == "Liste":
        items = (_render_children(item) for item in element.findall("Item"))
        return "\n".join(f"- {item}" for item in items if item)
    if tag == "Tableau":
        caption = _text(element.find("TitreRiche"))
        rows = (
            " | ".join(_render_children(cell) for cell in row.findall("Cellule"))
            for row in element.findall("Rangée")
        )
        return "\n".join(line for line in (caption, *rows) if line)
    if tag in _NOTES:
        title = _text(element.find("Titre"))
        body = _render_children(element)
        return f"{title} : {body}" if title else body
    if tag in _LINKS:
        title, url = _text(element.find("Titre")), element.attrib.get("URL", "")
        return f"{_LINKS[tag]} : {title} ({url})" if url else f"{_LINKS[tag]} : {title}"
    if tag == "Definition":
        return f"{_text(element.find('Titre'))} : {_render_children(element.find('Texte'))}"
    if tag == "OuSAdresser":
        title = _text(element.find("Titre"))
        return f"Où s'adresser : {title}" if title else ""
    return _text(element)


def _render_children(element: ET.Element | None) -> str:
    """Text of a container whose children are blocks (e.g. a list item with paragraphs)."""
    if element is None:
        return ""
    parts = [_render_block(c) for c in element if c.tag not in _SKIPPED]
    if not parts:
        return _text(element)
    return " ".join(p for p in parts if p)


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return _WHITESPACE.sub(" ", "".join(element.itertext())).strip()


def _modified(root: ET.Element) -> date | None:
    for element in root.findall(f"{DC}date"):
        if match := _MODIFIED.search(element.text or ""):
            return date.fromisoformat(match.group(1))
    return None


def _reference(element: ET.Element) -> LegalReference:
    url = element.attrib.get("URL", "")
    title = _text(element.find("Titre"))
    if match := _ARTICLE_URL.search(url):
        return LegalReference("article", url, title, legi_id=match.group(1))
    if match := _SECTION_URL.search(url):
        return LegalReference("section", url, title, legi_id=match.group(2), text_id=match.group(1))
    if match := _JORF_URL.search(url):
        return LegalReference("jorf_text", url, title, legi_id=match.group(1))
    return LegalReference("other", url, title)
