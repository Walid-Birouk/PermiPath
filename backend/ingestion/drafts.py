"""Source-independent chunk records produced by loaders, before storage."""

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal

SourceType = Literal["law", "treaty", "fact_sheet", "prefecture", "case_law"]
Authority = Literal["official", "secondary"]
NationalityScope = Literal["all", "algerian"]


@dataclass(frozen=True)
class SourceMeta:
    """Metadata assigned to every chunk of a source (from `sources/manifest.yaml`)."""

    authority: Authority = "official"
    nationality_scope: NationalityScope = "all"
    prefecture: str | None = None
    journey_stages: tuple[str, ...] = ()
    procedure_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChunkDraft:
    source_type: SourceType
    source_id: str
    chunk_index: int
    title: str
    url: str
    text: str
    lang: Literal["fr", "en"]
    meta: SourceMeta = field(default_factory=SourceMeta)
    in_force_from: date | None = None
    in_force_to: date | None = None
    source_updated_at: datetime | None = None

    @property
    def id(self) -> str:
        return chunk_id(self.source_type, self.source_id, self.chunk_index)

    @property
    def content_hash(self) -> str:
        return content_hash(self.title, self.text)


def chunk_id(source_type: str, source_id: str, chunk_index: int) -> str:
    """Deterministic chunk key (D-003)."""
    return f"{source_type}:{source_id}:{chunk_index}"


def content_hash(title: str, text: str) -> str:
    """Hash of the indexed content. Metadata (stages, tags, scope) is excluded: changing it is
    not a change in the official source and writes no changelog entry."""
    return hashlib.sha256(f"{title}\n\n{text}".encode()).hexdigest()
