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


class AnnexPoint(_Model):
    point: int = Field(ge=1)
    journey_stages: tuple[JourneyStage, ...]


class Annex(_Model):
    """A tabular annex indexed row by row (only the listed points)."""

    article_id: str = Field(pattern=r"^LEGIARTI\d+$")
    """Any version of the annex article; the version in force is resolved at ingestion."""
    label: str
    points: tuple[AnnexPoint, ...]
    note: str = ""


class IncludedArticle(_Model):
    """An article indexed although no selected sheet cites it."""

    article_id: str = Field(pattern=r"^LEGIARTI\d+$")
    """Any version of the article; the version in force is resolved at ingestion."""
    label: str
    """Article number, for readers of the manifest (e.g. `R431-17`)."""
    journey_stages: tuple[JourneyStage, ...]
    note: str = ""


class Code(_Model):
    text_id: str = Field(pattern=r"^LEGITEXT\d+$")
    label: str
    title: str
    exclude_articles: tuple[str, ...] = ()
    """LEGIARTI ids (any version) never indexed as whole articles, even when cited."""
    include_articles: tuple[IncludedArticle, ...] = ()
    """Articles indexed in addition to the cited ones. When an article is also cited, its
    journey stages are the union of both."""
    annexes: tuple[Annex, ...] = ()
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


class TreatyConsolidation(_Model):
    """A consolidated text kept in `sources/` (see `ingestion.consolidation`)."""

    source_id: str = Field(pattern=r"^[a-z0-9-]+$")
    path: str
    """Relative to the manifest's directory."""
    note: str = ""


class Treaties(_Model):
    nationality_scope: Literal["all", "algerian"] = "algerian"
    texts: tuple[TreatyText, ...]
    consolidations: tuple[TreatyConsolidation, ...] = ()


class Decision(_Model):
    """A court decision published on Légifrance (`consult/juri`), indexed as `case_law`."""

    id: str = Field(pattern=r"^(CETA|JURI)TEXT\d+$")
    title: str
    journey_stages: tuple[JourneyStage, ...]
    nationality_scope: Literal["all", "algerian"] = "all"
    note: str = ""

    def meta(self) -> SourceMeta:
        return SourceMeta(
            nationality_scope=self.nationality_scope, journey_stages=self.journey_stages
        )


class CaseLaw(_Model):
    """Decisions that settle how the indexed texts apply (e.g. which source governs)."""

    decisions: tuple[Decision, ...] = ()


class Manifest(_Model):
    version: Literal[1]
    reviewed_by: str | None = None
    reviewed_at: date | None = None
    fact_sheets: FactSheets
    laws: Laws
    treaties: Treaties
    case_law: CaseLaw = CaseLaw()


def load_manifest(path: Path = MANIFEST_PATH) -> Manifest:
    return Manifest.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
