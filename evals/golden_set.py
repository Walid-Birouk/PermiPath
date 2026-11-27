"""Golden question set: schema, loading and consistency rules.

The set lives in `evals/golden/<name>.yaml`. Questions may be drafted by anyone; the expected
answer fields (`must_cite`, `winning_source`, `key_facts`) are written and verified by the
maintainer from official sources only. An item counts as verified once `verified_by` and
`verified_at` are both set.

A source reference is `{source_type}:{source_id}`, the identity of a source in the `chunks`
table, e.g. `fact_sheet:F2713`, `law:LEGIARTI000042776689`,
`treaty:accord-franco-algerien-1968:article-7`.
"""

from collections import Counter
from datetime import date
from pathlib import Path
from typing import Annotated, Literal, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from ingestion.manifest import JourneyStage

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

Behaviour = Literal["answer", "refuse", "handoff", "abstain"]
"""answer: in scope, official sources exist. refuse: out of scope. handoff: personal or urgent
situation (general information + referral). abstain: in scope but no official source."""

SourceRef = Annotated[
    str, StringConstraints(pattern=r"^(law|treaty|fact_sheet|prefecture|case_law):\S+$")
]


def split_ref(ref: str) -> tuple[str, str]:
    """`fact_sheet:F2713` -> (`fact_sheet`, `F2713`)."""
    source_type, source_id = ref.split(":", 1)
    return source_type, source_id


class GoldenItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(pattern=r"^g\d{3}$")
    question: str = Field(min_length=10)
    lang: Literal["fr", "en"]
    stage: JourneyStage | None = None
    """Journey stage; empty for out-of-scope questions."""
    nationality: Literal["any", "algerian"] = "any"
    expected_behaviour: Behaviour
    must_cite: tuple[SourceRef, ...] = ()
    """Sources a correct answer cites (any chunk of the source counts)."""
    winning_source: SourceRef | None = None
    """Source that governs when sources conflict (Algerian cases: D-009)."""
    key_facts: tuple[str, ...] = ()
    """Facts a correct answer states, each checkable against `must_cite`."""
    origin: str = ""
    """Where the question comes from (e.g. a paraphrased forum question). No personal data."""
    notes: str = ""
    verified_by: str | None = None
    verified_at: date | None = None

    @property
    def verified(self) -> bool:
        return self.verified_by is not None and self.verified_at is not None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if (self.verified_by is None) != (self.verified_at is None):
            raise ValueError(f"{self.id}: set both verified_by and verified_at, or neither")
        if self.winning_source is not None and self.winning_source not in self.must_cite:
            raise ValueError(f"{self.id}: winning_source must be one of must_cite")
        if len(set(self.must_cite)) != len(self.must_cite):
            raise ValueError(f"{self.id}: duplicate source in must_cite")
        if self.expected_behaviour == "answer" and self.stage is None:
            raise ValueError(f"{self.id}: an answerable question needs a journey stage")
        if self.verified and self.expected_behaviour == "answer":
            if not self.must_cite or not self.key_facts:
                raise ValueError(f"{self.id}: a verified answer item needs must_cite and key_facts")
            if self.nationality == "algerian" and self.winning_source is None:
                raise ValueError(f"{self.id}: a verified Algerian answer item needs winning_source")
        return self


class GoldenSet(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: int = Field(ge=1)
    items: tuple[GoldenItem, ...] = ()

    @model_validator(mode="after")
    def _unique_ids(self) -> Self:
        duplicates = [i for i, n in Counter(item.id for item in self.items).items() if n > 1]
        if duplicates:
            raise ValueError(f"duplicate item ids: {duplicates}")
        return self

    def source_refs(self) -> set[str]:
        return {ref for item in self.items for ref in item.must_cite}


def set_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.yaml"


def load_golden_set(path: Path) -> GoldenSet:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return GoldenSet.model_validate(data)


def mix(golden: GoldenSet) -> dict[str, Counter[str]]:
    """Counts per behaviour, nationality, language, stage and verification state."""
    return {
        "behaviour": Counter(i.expected_behaviour for i in golden.items),
        "nationality": Counter(i.nationality for i in golden.items),
        "lang": Counter(i.lang for i in golden.items),
        "stage": Counter(i.stage or "none" for i in golden.items),
        "verified": Counter("verified" if i.verified else "draft" for i in golden.items),
    }
