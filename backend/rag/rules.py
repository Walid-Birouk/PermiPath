"""Read-only rules tool: sourced figures, deadlines, fees and documents (build step 5).

The `eligibility` node calls `RulesTool.lookup`. A question is matched to rule topics (what is
asked: hours, fees, deadlines...) and permits (which card) with French and English patterns.
A row is returned when its topic is asked; when the question names a permit, the row's permit
must be one of them. Rows are scoped like retrieval: Algerian rows for an Algerian national,
general rows otherwise (the agreement governs Algerian residence rights). Only verified rows
are used unless `include_unverified` is set (evaluation of drafts).

The tool only reads: one SELECT on `rules`.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from db.models import Rule

MAX_ROWS = 5


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(patterns), re.IGNORECASE)


TOPICS: dict[str, re.Pattern[str]] = {
    "work_limit": _rx(r"\bheures?\b", r"\bhours?\b", r"\btravaill", r"\bwork(ing)?\b"),
    "resources": _rx(
        r"\bressources\b", r"\bresources\b", r"moyens d'existence", r"\bfinanc", r"\bmoney\b"
    ),
    "deadline": _rx(
        r"\bquand\b",
        r"\bd[ée]lai",
        r"\bwhen\b",
        r"\bdeadline",
        r"date limite",
        r"\bavant (la )?fin\b",
        r"\bbefore\b",
    ),
    "fee": _rx(
        r"\bco[ûu]t",
        r"\bcombien co[ûu]te",
        r"\bprix\b",
        r"\btarif",
        r"\btaxe",
        r"\btimbre",
        r"\bfees?\b",
        r"\bcosts?\b",
        r"\bprice\b",
    ),
    "duration": _rx(
        r"\bdur[ée]e\b", r"\bvalable\b", r"\bvalidit", r"how long", r"\bduration\b", r"valid for"
    ),
    "salary_threshold": _rx(
        r"\bsalaire", r"\br[ée]mun[ée]ration", r"\bsalary", r"\bminimum\b", r"\bseuil", r"threshold"
    ),
    "residence_requirement": _rx(
        r"r[ée]sidence ininterrompue",
        r"certificat de r[ée]sidence de 10 ans",
        r"\b10 ans\b",
        r"\bdix ans\b",
        r"10-year",
        r"ten-year",
    ),
    "internship": _rx(r"\bstages?\b", r"\binternships?\b", r"\bstagiaire", r"\bgratification"),
    "documents": _rx(r"\bdocuments?\b", r"\bpi[èe]ces\b", r"\bjustificatifs?\b", r"\bpapers\b"),
}

PERMITS: dict[str, re.Pattern[str]] = {
    "vls_ts_etudiant": _rx(
        r"\bvls-ts\b", r"visa (de )?long s[ée]jour", r"long-stay", r"\bvalid(er|ate)\b"
    ),
    "cst_etudiant": _rx(r"\b[ée]tudiants?\b", r"\bstudents?\b"),
    "csp_etudiant": _rx(r"pluriannuel", r"multi-year"),
    "rece": _rx(
        r"recherche d'emploi",
        r"cr[ée]ation d'entreprise",
        r"job[- ]search",
        r"create a company",
        r"after graduating",
        r"fini mes [ée]tudes",
        r"fini mon master",
        r"apr[èe]s (mes|ses) [ée]tudes",
    ),
    "cst_salarie": _rx(
        r"\bsalari[ée]",
        r"\bemployee\b",
        r"changement de statut",
        r"change of status",
        r"promesse d'embauche",
    ),
    "csp_salarie": _rx(r"pluriannuel", r"multi-year"),
    "talent_salarie_qualifie": _rx(r"\btalent\b"),
    "stage_etudiant": _rx(r"\bstages?\b", r"\binternships?\b"),
    "cr_etudiant": _rx(r"\b[ée]tudiants?\b", r"\bstudents?\b"),
    "cr_salarie": _rx(
        r"\bsalari[ée]", r"offre d'emploi", r"\bemployee\b", r"changement de statut", r"job offer"
    ),
    "cr_10_ans": _rx(r"\b10 ans\b", r"\bdix ans\b", r"10-year", r"ten-year"),
}


@dataclass(frozen=True)
class Match:
    topics: frozenset[str]
    permits: frozenset[str]


def match(question: str) -> Match:
    return Match(
        frozenset(t for t, rx in TOPICS.items() if rx.search(question)),
        frozenset(p for p, rx in PERMITS.items() if rx.search(question)),
    )


@dataclass(frozen=True)
class RuleRow:
    id: str
    permit: str
    nationality_scope: str
    topic: str
    label_fr: str
    label_en: str
    value: str
    quote: str
    source_type: str
    source_id: str
    source_title: str
    url: str
    source_checked_at: datetime | None
    verified_at: date | None

    @property
    def ref(self) -> str:
        return f"rule:{self.id}"

    @property
    def source_ref(self) -> str:
        return f"{self.source_type}:{self.source_id}"

    def label(self, lang: Literal["fr", "en"]) -> str:
        return self.label_fr if lang == "fr" else self.label_en

    def text(self, lang: Literal["fr", "en"] = "fr") -> str:
        """What the model sees and the citation checker reads."""
        return f"{self.label(lang)}: {self.value}\n{self.source_title}: « {self.quote} »"


def select_rows(rows: Sequence[RuleRow], found: Match, algerian: bool) -> list[RuleRow]:
    scope = "algerian" if algerian else "all"
    selected = [
        r
        for r in rows
        if r.nationality_scope == scope
        and r.topic in found.topics
        and (not found.permits or r.permit in found.permits)
    ]
    return selected[:MAX_ROWS]


@dataclass
class RulesTool:
    engine: Engine
    include_unverified: bool = False

    def lookup(self, question: str, algerian: bool) -> list[RuleRow]:
        found = match(question)
        if not found.topics:
            return []
        query = select(Rule).where(Rule.topic.in_(sorted(found.topics))).order_by(Rule.id)
        if not self.include_unverified:
            query = query.where(Rule.verified_at.is_not(None))
        with Session(self.engine) as session:
            rows = [_row(r) for r in session.execute(query).scalars()]
        return select_rows(rows, found, algerian)


def _row(rule: Rule) -> RuleRow:
    return RuleRow(
        id=rule.id,
        permit=rule.permit,
        nationality_scope=rule.nationality_scope,
        topic=rule.topic,
        label_fr=rule.label_fr,
        label_en=rule.label_en,
        value=rule.value,
        quote=rule.quote,
        source_type=rule.source_type,
        source_id=rule.source_id,
        source_title=rule.source_title,
        url=rule.url,
        source_checked_at=rule.source_checked_at,
        verified_at=rule.verified_at,
    )
