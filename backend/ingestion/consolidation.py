"""Consolidated treaty texts maintained in `sources/` (e.g. the Franco-Algerian agreement of
27 December 1968 with its three amendments) -> one chunk group per provision in force.

A consolidation file records, for every provision, the current wording and the instruments
that set it. It is indexed only once a maintainer has verified it (`status: verified`).
"""

from datetime import date
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ingestion.chunker import MAX_CHUNK_CHARS, TextChunk, pack
from ingestion.drafts import ChunkDraft, SourceMeta
from ingestion.manifest import JourneyStage

PART_LABELS = {"accord": "Accord", "protocole": "Protocole"}


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Instrument(_Model):
    id: str
    title: str
    signed_on: date
    in_force_from: date | None
    in_force_note: str = ""
    publication: str
    publication_url: str
    transcribed_from: str
    transcription_url: str


class Change(_Model):
    instrument: str
    change: Literal["original", "inserted", "replaced", "amended", "repealed"]
    provision: str = ""
    """Provision of the instrument that made the change (e.g. "Article 5, I à VI")."""
    detail: str = ""


class Provision(_Model):
    id: str = Field(pattern=r"^[a-z0-9-]+$")
    part: Literal["accord", "protocole"]
    label: str
    status: Literal["in_force", "repealed"] = "in_force"
    journey_stages: tuple[JourneyStage, ...] = ()
    text: str = ""
    history: tuple[Change, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _text_matches_status(self) -> Self:
        if (self.status == "in_force") != bool(self.text.strip()):
            raise ValueError(f"{self.id}: text is required in force and absent when repealed")
        last = self.history[-1].change
        if (self.status == "repealed") != (last == "repealed"):
            raise ValueError(f"{self.id}: status does not match its last change ({last})")
        return self


class Consolidation(_Model):
    version: Literal[1]
    status: Literal["draft", "verified"]
    verified_by: str | None = None
    verified_at: date | None = None
    consolidated_at: date
    title: str
    short_title: str
    instruments: tuple[Instrument, ...]
    provisions: tuple[Provision, ...]
    not_consolidated: tuple[str, ...] = ()
    editorial_notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.status == "verified" and not (self.verified_by and self.verified_at):
            raise ValueError("a verified consolidation needs verified_by and verified_at")
        known = {i.id for i in self.instruments}
        if len(known) != len(self.instruments):
            raise ValueError("duplicate instrument id")
        ids = [p.id for p in self.provisions]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate provision id")
        unknown = {c.instrument for p in self.provisions for c in p.history} - known
        if unknown:
            raise ValueError(f"unknown instruments in history: {sorted(unknown)}")
        return self

    def instrument(self, instrument_id: str) -> Instrument:
        return next(i for i in self.instruments if i.id == instrument_id)


def load_consolidation(path: Path) -> Consolidation:
    return Consolidation.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def consolidation_drafts(
    consolidation: Consolidation,
    source_id: str,
    meta: SourceMeta,
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[ChunkDraft]:
    """Drafts for the provisions in force. Each chunk ends with the instruments that set its
    wording; its URL is the official publication of the last of them."""
    drafts: list[ChunkDraft] = []
    for provision in consolidation.provisions:
        if provision.status != "in_force":
            continue
        instruments = [consolidation.instrument(c.instrument) for c in provision.history]
        current = instruments[-1]
        part = PART_LABELS[provision.part]
        path = (f"{consolidation.short_title} (texte consolidé)", part, provision.label)
        origin = "Rédaction issue de : " + " ; ".join(
            dict.fromkeys(f"{i.title} ({i.publication})" for i in instruments)
        )
        bodies = pack([*provision.text.split("\n"), origin], max_chars)
        start_dates = [i.in_force_from for i in instruments]
        for index, body in enumerate(bodies):
            drafts.append(
                ChunkDraft(
                    source_type="treaty",
                    source_id=f"{source_id}:{provision.id}",
                    chunk_index=index,
                    title=f"{consolidation.short_title}, {_citation(provision)}",
                    url=current.publication_url,
                    text=TextChunk(path, body).text,
                    lang="fr",
                    meta=SourceMeta(
                        authority=meta.authority,
                        nationality_scope=meta.nationality_scope,
                        journey_stages=provision.journey_stages,
                    ),
                    # Unknown when an instrument's entry-into-force date is not established.
                    in_force_from=None if None in start_dates else max(d for d in start_dates if d),
                )
            )
    return drafts


def _citation(provision: Provision) -> str:
    if provision.part == "protocole":
        return f"protocole, {provision.label.split('.')[0].replace('Titre', 'titre', 1)}"
    return provision.label.replace("Article", "article", 1)
