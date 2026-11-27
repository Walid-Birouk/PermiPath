"""Légifrance article version -> chunk drafts. One article = one chunk; an article longer than
the chunk limit is split into consecutive chunks that keep the article id (CLAUDE.md, PLAN.md)."""

from datetime import date

from ingestion.chunker import MAX_CHUNK_CHARS, TextChunk, pack
from ingestion.drafts import ChunkDraft, SourceMeta, SourceType
from ingestion.legifrance.client import ArticleVersion


def article_drafts(
    article: ArticleVersion,
    on: date,
    meta: SourceMeta,
    code_label: str = "CESEDA",
    source_type: SourceType = "law",
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[ChunkDraft]:
    """Chunks keyed by the article's chronical id, so a new version of the article updates the
    same chunks (and writes a `modified` changelog entry) instead of adding new ones."""
    heading = f"Article {article.num}" if article.num else "Article"
    blocks = [article.text, f"Nota : {article.nota}" if article.nota else ""]
    path = (*article.section_path, heading)
    return [
        ChunkDraft(
            source_type=source_type,
            source_id=article.cid or article.id,
            chunk_index=index,
            title=f"{code_label}, article {article.num}".strip(),
            url=article.url(on),
            text=TextChunk(path, body).text,
            lang="fr",
            meta=meta,
            in_force_from=article.in_force_from,
            in_force_to=article.in_force_to,
        )
        for index, body in enumerate(pack([b for b in blocks if b], max_chars))
    ]
