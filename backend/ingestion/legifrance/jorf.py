"""Texts published in the Journal officiel (`consult/jorf`) -> treaty clause chunks.

Used for the Franco-Algerian agreement of 27 December 1968 and its amendments, published by
decree. A clause is a centred "Article ..." heading and the text up to the next one; the text
before the first heading is the preamble. One clause = one chunk (split only if long, same
source id).
"""

import re
from dataclasses import dataclass
from typing import Any

from ingestion.chunker import MAX_CHUNK_CHARS, TextChunk, pack
from ingestion.drafts import ChunkDraft, SourceMeta
from ingestion.legifrance.client import _html_to_text

JORF_TEXT_URL = "https://www.legifrance.gouv.fr/jorf/id/{cid}"
"""Pattern used by the service-public sheets' references to JORF texts."""

_HEADING = re.compile(
    r'<p\s+align="center">\s*(?:<br\s*/?>)?\s*((?:Article|Art\.)\s[^<]{1,40}?)\s*</p>',
    re.IGNORECASE,
)
_DECREE_ARTICLE = re.compile(r"^\s*Art\.\s*(\d+)")
PREAMBLE = "Préambule"


@dataclass(frozen=True)
class Clause:
    path: tuple[str, ...]
    text: str


def jorf_clauses(text: dict[str, Any]) -> list[Clause]:
    clauses: list[Clause] = []
    for article in sorted(text.get("articles") or [], key=_reading_order):
        html = str(article.get("content") or "")
        if not _html_to_text(html):
            continue
        label = _label(article)
        parts = _HEADING.split(html)
        preamble, rest = parts[0], parts[1:]
        if body := _clean(preamble):
            clauses.append(Clause((label, PREAMBLE) if rest else (label,), body))
        for heading, content in zip(rest[::2], rest[1::2], strict=True):
            clauses.append(Clause((label, _clean(heading)), _clean(content)))
    return clauses


def jorf_drafts(
    text: dict[str, Any], meta: SourceMeta, max_chars: int = MAX_CHUNK_CHARS
) -> list[ChunkDraft]:
    cid = str(text.get("cid") or text["id"])
    title = _clean(str(text.get("title") or cid))
    pieces = [
        TextChunk(clause.path, body)
        for clause in jorf_clauses(text)
        for body in pack([clause.text], max_chars)
    ]
    return [
        ChunkDraft(
            source_type="treaty",
            source_id=cid,
            chunk_index=index,
            title=title,
            url=JORF_TEXT_URL.format(cid=cid),
            text=piece.text,
            lang="fr",
            meta=meta,
        )
        for index, piece in enumerate(pieces)
    ]


def _reading_order(article: dict[str, Any]) -> tuple[int, int, str]:
    """Decree articles by number, then annexes and unnumbered parts (by id)."""
    num = str(article.get("num") or "")
    if num.isdigit():
        return (0, int(num), "")
    if match := _DECREE_ARTICLE.match(_html_to_text(str(article.get("content") or ""))):
        return (0, int(match.group(1)), "")
    return (1, 0, str(article.get("id")))


def _label(article: dict[str, Any]) -> str:
    num = str(article.get("num") or "")
    if num.isdigit():
        return f"Décret, article {num}"
    if _reading_order(article)[0] == 0:
        return f"Décret, article {_reading_order(article)[1]}"
    return num or "Texte publié"


def _clean(html: str) -> str:
    text = _html_to_text(html)
    return re.sub(r"\s*\n\s*", "\n", re.sub(r"[ \t‎]+", " ", text)).strip()
