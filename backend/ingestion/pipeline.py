"""Ingestion pipeline: manifest -> loaders -> chunk drafts per source type.

Each loader returns a `LoadResult`. A source type is `complete` only when every source of that
type loaded without error; only complete types can remove stored chunks (see `ingestion.diff`).
"""

import io
import zipfile
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date

import httpx2

from ingestion.drafts import ChunkDraft, SourceMeta, SourceType
from ingestion.legifrance.client import LegifranceClient, LegifranceError, NotFoundError
from ingestion.legifrance.jorf import jorf_drafts
from ingestion.legifrance.loader import article_drafts
from ingestion.legifrance.toc import TocIndex, resolve
from ingestion.manifest import Code, Manifest
from ingestion.service_public.loader import sheet_drafts
from ingestion.service_public.parser import Sheet, parse_sheet_bytes

FEED_TIMEOUT_S = 120.0


@dataclass
class LoadResult:
    source_type: SourceType
    drafts: list[ChunkDraft] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.errors

    @property
    def sources(self) -> set[tuple[str, str]]:
        return {(d.source_type, d.source_id) for d in self.drafts}


# --- service-public ---------------------------------------------------------------------


def download_feed(url: str, http: httpx2.Client | None = None) -> bytes:
    client = http or httpx2.Client(timeout=FEED_TIMEOUT_S, follow_redirects=True)
    try:
        response = client.get(url)
        response.raise_for_status()
        return response.content
    finally:
        if http is None:
            client.close()


def read_sheets(manifest: Manifest, feed_zip: bytes) -> tuple[list[Sheet], list[str]]:
    sheets: list[Sheet] = []
    errors: list[str] = []
    with zipfile.ZipFile(io.BytesIO(feed_zip)) as archive:
        names = set(archive.namelist())
        for entry in manifest.fact_sheets.sheets:
            member = f"{entry.id}.xml"
            if member not in names:
                errors.append(f"{entry.id}: not in the feed")
                continue
            try:
                sheets.append(parse_sheet_bytes(archive.read(member)))
            except (ValueError, SyntaxError) as exc:
                errors.append(f"{entry.id}: {exc}")
    return sheets, errors


def load_fact_sheets(manifest: Manifest, sheets: list[Sheet], errors: list[str]) -> LoadResult:
    result = LoadResult("fact_sheet", errors=list(errors))
    entries = {e.id: e for e in manifest.fact_sheets.sheets}
    for sheet in sheets:
        result.drafts.extend(sheet_drafts(sheet, entries[sheet.id].meta()))
    return result


# --- Légifrance codes -------------------------------------------------------------------


def load_laws(
    manifest: Manifest, sheets: Iterable[Sheet], client: LegifranceClient, on: date
) -> LoadResult:
    result = LoadResult("law")
    sheets = list(sheets)
    stages = {e.id: e.journey_stages for e in manifest.fact_sheets.sheets}
    for code in manifest.laws.codes:
        try:
            index = TocIndex(client.table_of_contents(code.text_id, on))
        except LegifranceError as exc:
            result.errors.append(f"{code.label} table of contents: {exc}")
            continue
        citing: dict[str, set[str]] = {}
        for sheet in sheets:
            ids = _cited_articles(index, code, sheet, result)
            for article_id in ids:
                citing.setdefault(article_id, set()).update(stages.get(sheet.id, ()))
        _fetch_articles(client, code, citing, on, result)
    return result


def _cited_articles(index: TocIndex, code: Code, sheet: Sheet, result: LoadResult) -> list[str]:
    sections = [
        r.legi_id
        for r in sheet.references
        if r.kind == "section" and r.text_id == code.text_id and r.legi_id
    ]
    articles = [
        r.legi_id
        for r in sheet.references
        if r.kind == "article" and r.legi_id and r.title.startswith(code.title)
    ]
    resolution = resolve(index, sections, articles)
    result.notes.extend(
        f"{sheet.id}: section {cid} not in force in {code.label}"
        for cid in resolution.unresolved_sections
    )
    return [a for a in resolution.article_ids if a not in code.exclude_articles]


def _fetch_articles(
    client: LegifranceClient,
    code: Code,
    citing: dict[str, set[str]],
    on: date,
    result: LoadResult,
) -> None:
    seen: set[str] = set()
    for article_id, stage_set in citing.items():
        try:
            article = client.version_in_force(article_id, on)
        except NotFoundError as exc:
            result.notes.append(f"{code.label} {article_id}: {exc}")
            continue
        except LegifranceError as exc:
            result.errors.append(f"{code.label} {article_id}: {exc}")
            continue
        key = article.cid or article.id
        if key in seen or article.id in code.exclude_articles:
            continue
        seen.add(key)
        meta = SourceMeta(journey_stages=tuple(s for s in _STAGE_ORDER if s in stage_set))
        result.drafts.extend(article_drafts(article, on, meta, code_label=code.label))


_STAGE_ORDER = ("arriving", "renewing", "working", "finishing", "job_offer")


# --- treaties ---------------------------------------------------------------------------


def load_treaties(manifest: Manifest, client: LegifranceClient) -> LoadResult:
    result = LoadResult("treaty")
    meta = SourceMeta(nationality_scope=manifest.treaties.nationality_scope)
    for text in manifest.treaties.texts:
        if text.status != "included":
            result.notes.append(f"{text.jorf_cid}: {text.status} ({text.title})")
            continue
        try:
            drafts = jorf_drafts(client.jorf_text(text.jorf_cid), meta)
        except LegifranceError as exc:
            result.errors.append(f"{text.jorf_cid}: {exc}")
            continue
        if not drafts:
            result.errors.append(f"{text.jorf_cid}: no text returned")
        result.drafts.extend(drafts)
    return result
