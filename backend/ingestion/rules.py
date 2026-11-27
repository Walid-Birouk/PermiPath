"""Loads `sources/rules.yaml` into the `rules` table after checking every row against the index.

Checks (a row failing any of them is an error and nothing is loaded):

1. the source (`{source_type}:{source_id}`) has chunks in the index;
2. the quote appears in the source's title or text, compared after normalisation (Unicode NFC,
   no-break spaces as spaces, typographic apostrophes as `'`, runs of whitespace collapsed);
3. every figure of the value appears in the quote (`rag.citations.figures`, French number
   words included): a value cannot state a figure its quote does not support (rule 6).

The table is replaced as a whole in one transaction; `url`, `source_title` and
`source_checked_at` come from the indexed chunks, not from the file.
"""

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from db.models import Chunk, Rule
from ingestion.manifest import JourneyStage
from rag.citations import figures

RULES_PATH = Path(__file__).resolve().parents[2] / "sources" / "rules.yaml"

Permit = Literal[
    "vls_ts_etudiant",
    "cst_etudiant",
    "csp_etudiant",
    "rece",
    "cst_salarie",
    "csp_salarie",
    "talent_salarie_qualifie",
    "stage_etudiant",
    "cr_etudiant",
    "cr_salarie",
    "cr_10_ans",
]
Topic = Literal[
    "work_limit",
    "resources",
    "deadline",
    "fee",
    "duration",
    "salary_threshold",
    "residence_requirement",
    "internship",
    "documents",
]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Label(_Model):
    fr: str = Field(min_length=3)
    en: str = Field(min_length=3)


class RuleEntry(_Model):
    id: str = Field(pattern=r"^[a-z0-9]+(-[a-z0-9]+)*$")
    permit: Permit
    nationality_scope: Literal["all", "algerian"]
    topic: Topic
    stage: JourneyStage
    label: Label
    value: str = Field(min_length=1)
    quote: str = Field(min_length=5)
    source: str = Field(pattern=r"^(law|treaty|fact_sheet|prefecture|case_law):\S+$")
    verified_by: str | None = None
    verified_at: date | None = None

    @model_validator(mode="after")
    def _verification_together(self) -> Self:
        if (self.verified_by is None) != (self.verified_at is None):
            raise ValueError(f"{self.id}: set both verified_by and verified_at, or neither")
        return self


class RulesFile(_Model):
    version: Literal[1]
    rules: tuple[RuleEntry, ...]

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        duplicates = [i for i, n in Counter(r.id for r in self.rules).items() if n > 1]
        if duplicates:
            raise ValueError(f"duplicate rule ids: {duplicates}")
        return self


@dataclass(frozen=True)
class IndexedSource:
    text: str
    """Titles and texts of all the source's chunks, normalised."""
    title: str
    url: str
    fetched_at: datetime


def load_rules_file(path: Path = RULES_PATH) -> RulesFile:
    return RulesFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = text.replace(chr(0x00A0), " ").replace(chr(0x202F), " ").replace(chr(0x2019), "'")
    return re.sub(r"\s+", " ", text).strip()


def check_rules(rules: RulesFile, sources: dict[str, IndexedSource]) -> list[str]:
    errors: list[str] = []
    for rule in rules.rules:
        source = sources.get(rule.source)
        if source is None:
            errors.append(f"{rule.id}: source {rule.source} is not in the index")
            continue
        if normalise(rule.quote) not in source.text:
            errors.append(f"{rule.id}: quote not found in {rule.source}")
            continue
        missing = sorted(figures(rule.value) - figures(rule.quote, number_words=True))
        if missing:
            errors.append(f"{rule.id}: value states {', '.join(missing)}, absent from the quote")
    return errors


def indexed_sources(session: Session, refs: set[str]) -> dict[str, IndexedSource]:
    pairs = [tuple(ref.split(":", 1)) for ref in refs]
    by_ref: dict[str, list[Chunk]] = {}
    for source_type, source_id in pairs:
        chunks = session.execute(
            select(Chunk)
            .where(Chunk.source_type == source_type, Chunk.source_id == source_id)
            .order_by(Chunk.chunk_index)
        ).scalars()
        found = list(chunks)
        if found:
            by_ref[f"{source_type}:{source_id}"] = found
    return {
        ref: IndexedSource(
            text=normalise(" ".join(f"{c.title} {c.text}" for c in chunks)),
            title=chunks[0].title,
            url=chunks[0].url,
            fetched_at=max(c.fetched_at for c in chunks),
        )
        for ref, chunks in by_ref.items()
    }


def sync_rules(session: Session, rules: RulesFile, now: datetime) -> tuple[int, list[str]]:
    """Checks and replaces the `rules` table. Returns (rows loaded, errors); nothing is written
    when there is an error."""
    sources = indexed_sources(session, {r.source for r in rules.rules})
    errors = check_rules(rules, sources)
    if errors:
        return 0, errors
    session.execute(delete(Rule))
    for rule in rules.rules:
        source = sources[rule.source]
        source_type, source_id = rule.source.split(":", 1)
        session.add(
            Rule(
                id=rule.id,
                permit=rule.permit,
                nationality_scope=rule.nationality_scope,
                topic=rule.topic,
                stage=rule.stage,
                label_fr=rule.label.fr,
                label_en=rule.label.en,
                value=rule.value,
                quote=rule.quote,
                source_type=source_type,
                source_id=source_id,
                source_title=source.title,
                url=source.url,
                source_checked_at=source.fetched_at,
                verified_by=rule.verified_by,
                verified_at=rule.verified_at,
                loaded_at=now,
            )
        )
    return len(rules.rules), []
