"""`sources/manifest.yaml`: the list of indexed sources and their metadata, reviewed by the
maintainer before embedding (PLAN.md, build step 2)."""

from datetime import date
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from app.config import ROOT_ENV_FILE
from ingestion.drafts import SourceMeta

MANIFEST_PATH = ROOT_ENV_FILE.parent / "sources" / "manifest.yaml"

JourneyStage = Literal["arriving", "renewing", "working", "finishing", "job_offer", "settling"]
"""Same values as `db.models.JOURNEY_STAGES` (checked by tests)."""


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SheetEntry(_Model):
    id: str = Field(pattern=r"^F\d+$")
    title: str
    journey_stages: tuple[JourneyStage, ...]
    nationality_scope: Literal["all", "algerian"] = "all"
    note: str = ""

    def meta(self) -> SourceMeta:
        return SourceMeta(
            nationality_scope=self.nationality_scope, journey_stages=self.journey_stages
        )


class FactSheets(_Model):
    feed_url: str
    sheets: tuple[SheetEntry, ...]
    excluded: tuple[SheetEntry, ...] = ()


class Code(_Model):
    text_id: str = Field(pattern=r"^LEGITEXT\d+$")
    label: str
    title: str
    exclude_articles: tuple[str, ...] = ()
    """LEGIARTI ids (any version) never indexed, even when cited."""
    note: str = ""


class Laws(_Model):
    """Articles of these codes cited by the selected sheets (sections expanded through the
    table of contents). Each article inherits the journey stages of the sheets citing it."""

    codes: tuple[Code, ...]


class TreatyText(_Model):
    jorf_cid: str = Field(pattern=r"^JORFTEXT\d+$")
    title: str
    status: Literal["included", "pending"]
    note: str = ""


class Treaties(_Model):
    nationality_scope: Literal["all", "algerian"] = "algerian"
    texts: tuple[TreatyText, ...]


class Manifest(_Model):
    version: Literal[1]
    reviewed_by: str | None = None
    reviewed_at: date | None = None
    fact_sheets: FactSheets
    laws: Laws
    treaties: Treaties


def load_manifest(path: Path = MANIFEST_PATH) -> Manifest:
    return Manifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
