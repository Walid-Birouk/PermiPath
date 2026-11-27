"""Service-public sheet -> chunk drafts."""

from datetime import UTC, datetime, time

from ingestion.chunker import MAX_CHUNK_CHARS, chunk_tree
from ingestion.drafts import ChunkDraft, SourceMeta
from ingestion.service_public.parser import Sheet


def sheet_drafts(
    sheet: Sheet, meta: SourceMeta, max_chars: int = MAX_CHUNK_CHARS
) -> list[ChunkDraft]:
    updated = datetime.combine(sheet.modified, time(), UTC) if sheet.modified else None
    return [
        ChunkDraft(
            source_type="fact_sheet",
            source_id=sheet.id,
            chunk_index=index,
            title=sheet.title,
            url=sheet.url,
            text=chunk.text,
            lang="fr",
            meta=meta,
            source_updated_at=updated,
        )
        for index, chunk in enumerate(chunk_tree(sheet.root, max_chars))
    ]
