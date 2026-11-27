"""Which source governs (D-009) and the order in which retrieved sources are presented.

For an Algerian national:

- residence rights (which permit, conditions, work rights, duration) are set by the Franco-
  Algerian agreement, which governs them completely (Conseil d'État, 2 March 2026, n° 500835):
  the agreement and the case law come first, then Algerian-specific sheets, then the rest;
- general procedure (where and how to apply, online service, documents, fees, deadlines not set
  by the agreement) follows the general rules: fact sheets and CESEDA come first.

The topic is `rights` unless the question carries an explicit procedural marker; when in doubt
the agreement is presented first, never silently replaced by a CESEDA rule. For other
questions the retrieval order is kept and CESEDA / service-public govern.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

Topic = Literal["rights", "procedure"]
Governing = Literal["agreement", "general"]

_PROCEDURE = re.compile(
    r"\b(o[ùu]|where|en ligne|online|anef|internet|t[ée]l[ée]service|d[ée]poser|submit|"
    r"documents?|pi[èe]ces|justificatifs?|co[ûu]t|fees?|timbres?|tax(e|es)?|"
    r"rendez-vous|appointment|which office|quel(le)? (pr[ée]fecture|guichet))\b",
    re.IGNORECASE,
)

AGREEMENT_FIRST = ("treaty", "case_law")
GOVERNING_CANDIDATES = 3
"""When the agreement governs, the best chunks of the agreement and case law are retrieved
separately and merged, so the governing source is in the pool even when the hybrid ranking
misses it."""


class Ranked(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def source_type(self) -> str: ...

    @property
    def nationality_scope(self) -> str: ...


@dataclass(frozen=True)
class PrecedenceDecision:
    governing: Governing
    topic: Topic | None
    """None when the question is not about an Algerian national."""


def topic_of(question: str) -> Topic:
    return "procedure" if _PROCEDURE.search(question) else "rights"


def decide(question: str, algerian: bool) -> PrecedenceDecision:
    if not algerian:
        return PrecedenceDecision("general", None)
    topic = topic_of(question)
    return PrecedenceDecision("agreement" if topic == "rights" else "general", topic)


def _priority(chunk: Ranked, decision: PrecedenceDecision) -> int:
    if decision.topic is None:
        return 0
    if decision.topic == "rights":
        if chunk.source_type in AGREEMENT_FIRST:
            return 0
        return 1 if chunk.nationality_scope == "algerian" else 2
    # procedure: Algerian-specific procedure sheets, then general sheets and CESEDA, then
    # the agreement (still shown: it sets which certificate is requested).
    if chunk.source_type == "fact_sheet":
        return 0 if chunk.nationality_scope == "algerian" else 1
    return 2 if chunk.source_type == "law" else 3


def merge[T: Ranked](chunks: Sequence[T], extra: Sequence[T]) -> list[T]:
    """`chunks` followed by the chunks of `extra` not already present (by id)."""
    seen = {c.id for c in chunks}
    return [*chunks, *(c for c in extra if c.id not in seen)]


def order[T: Ranked](chunks: Sequence[T], decision: PrecedenceDecision) -> list[T]:
    """Stable reorder by precedence priority; retrieval order is kept within a priority."""
    return [
        c for _, c in sorted(enumerate(chunks), key=lambda p: (_priority(p[1], decision), p[0]))
    ]
