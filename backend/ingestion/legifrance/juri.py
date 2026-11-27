"""Court decisions (`consult/juri`) -> `case_law` chunks.

A decision is indexed as published on Légifrance (pseudonymised there). Its paragraphs (the
`<br/>`-separated blocks of `texteHtml`) are packed into chunks that keep the decision id, so a
cited point ("considérant") stays whole unless it exceeds the chunk limit.
"""

import re
from typing import Any

from ingestion.chunker import MAX_CHUNK_CHARS, TextChunk, pack
from ingestion.drafts import ChunkDraft, SourceMeta
from ingestion.legifrance.client import _html_to_text, _parse_date

DECISION_URLS = {
    "CETATEXT": "https://www.legifrance.gouv.fr/ceta/id/{id}",
    "JURITEXT": "https://www.legifrance.gouv.fr/juri/id/{id}",
}
_BREAK = re.compile(r"<br\s*/?>", re.IGNORECASE)


def decision_url(decision_id: str) -> str:
    return DECISION_URLS[decision_id[:8]].format(id=decision_id)


def decision_paragraphs(text: dict[str, Any]) -> list[str]:
    html = str(text.get("texteHtml") or "")
    if not html:
        return [p for p in [str(text.get("texte") or "").strip()] if p]
    return [p for p in (_html_to_text(part) for part in _BREAK.split(html)) if p]


def decision_drafts(
    text: dict[str, Any], meta: SourceMeta, max_chars: int = MAX_CHUNK_CHARS
) -> list[ChunkDraft]:
    decision_id = str(text["id"])
    title = str(text.get("titre") or decision_id)
    if ecli := text.get("ecli"):
        title = f"{title} ({ecli})"
    return [
        ChunkDraft(
            source_type="case_law",
            source_id=decision_id,
            chunk_index=index,
            title=title,
            url=decision_url(decision_id),
            text=TextChunk((title,), body).text,
            lang="fr",
            meta=meta,
            in_force_from=_parse_date(text.get("dateTexte")),
        )
        for index, body in enumerate(pack(decision_paragraphs(text), max_chars))
    ]
