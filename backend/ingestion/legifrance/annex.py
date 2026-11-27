"""Tabular CESEDA annexes (e.g. Annexe 10, supporting documents per residence permit) -> one
chunk group per table row ("point"), citable as "CESEDA, annexe 10, point 25"."""

import html
import re
from dataclasses import dataclass
from datetime import date

from ingestion.chunker import MAX_CHUNK_CHARS, TextChunk, pack
from ingestion.drafts import ChunkDraft, SourceMeta
from ingestion.legifrance.client import ArticleVersion

_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.IGNORECASE)
_CELL = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.IGNORECASE)
_BREAK = re.compile(r"<br\s*/?>|</p>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")


@dataclass(frozen=True)
class AnnexRow:
    point: int
    category: str
    permit: str
    references: str
    """CESEDA articles listed for the row, as written (e.g. "L. 422-1 L. 422-2")."""
    body: str
    """Supporting documents, one line per paragraph of the source cell."""


def annex_rows(article: ArticleVersion) -> list[AnnexRow]:
    """Rows of the annex table whose first cell is a point number; the header row is skipped."""
    rows: list[AnnexRow] = []
    for raw in _ROW.findall(article.text_html):
        cells = [_cell_text(c) for c in _CELL.findall(raw)]
        if len(cells) < 5 or not cells[0].isdigit():
            continue
        rows.append(
            AnnexRow(
                point=int(cells[0]),
                category=_one_line(cells[1]),
                permit=_one_line(cells[2]),
                references=_one_line(cells[3]),
                body=cells[4],
            )
        )
    return rows


def annex_drafts(
    article: ArticleVersion,
    annex_label: str,
    points: dict[int, SourceMeta],
    on: date,
    code_label: str = "CESEDA",
    max_chars: int = MAX_CHUNK_CHARS,
) -> tuple[list[ChunkDraft], list[int]]:
    """Drafts for the selected points, and the selected points missing from the table."""
    by_point = {row.point: row for row in annex_rows(article)}
    drafts: list[ChunkDraft] = []
    for point, meta in points.items():
        row = by_point.get(point)
        if row is None:
            continue
        path = (annex_label, f"Point {point} : {row.category}", row.permit)
        intro = f"Référence du {code_label} : {row.references}"
        for index, body in enumerate(pack([intro, *row.body.split("\n")], max_chars)):
            drafts.append(
                ChunkDraft(
                    source_type="law",
                    source_id=f"{article.cid or article.id}-point-{point}",
                    chunk_index=index,
                    title=f"{code_label}, {annex_label.lower()}, point {point}",
                    url=article.url(on),
                    text=TextChunk(path, body).text,
                    lang="fr",
                    meta=meta,
                    in_force_from=article.in_force_from,
                    in_force_to=article.in_force_to,
                )
            )
    return drafts, sorted(set(points) - set(by_point))


def _cell_text(cell_html: str) -> str:
    text = html.unescape(_TAG.sub("", _BREAK.sub("\n", cell_html)))
    lines = (re.sub(r"[ \t\u00a0]+", " ", line).strip() for line in text.split("\n"))
    return "\n".join(line for line in lines if line)


def _one_line(text: str) -> str:
    return " ".join(text.split())
