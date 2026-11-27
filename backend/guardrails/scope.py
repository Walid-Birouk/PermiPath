"""Routes a question before retrieval: in scope, out of scope (refuse) or handoff.

Categories follow CLAUDE.md, Scope.

Rules are explicit keyword patterns (French and English), checked in this order:

1. Handoff: urgent or personal-legal situations (OQTF, refusal and appeal, expired permit,
   detention). A general answer is not enough; the user is referred to a lawyer, student
   associations or the school's international office.
2. Out of scope: asylum, family reunification, naturalisation, and permit categories outside
   the student-to-employee journey. Refused with an explanation.
3. Otherwise in scope. A question in scope may still be personal ("in my case"): `personal`
   is set so the answer adds the handoff notice (information, not advice; rule 1).

The text is not stored (rule 5).
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

Route = Literal["in_scope", "out_of_scope", "handoff"]


@dataclass(frozen=True)
class ScopeDecision:
    route: Route
    reason: str | None = None
    """Matched category, e.g. `asylum` or `oqtf`; None when in scope."""
    personal: bool = False


def _rx(*patterns: str) -> re.Pattern[str]:
    return re.compile("|".join(patterns), re.IGNORECASE)


HANDOFF: dict[str, re.Pattern[str]] = {
    "oqtf": _rx(
        r"\boqtf\b",
        r"obligation de quitter",
        r"order to leave",
        r"\bexpuls",
        r"deportation",
    ),
    "refusal_appeal": _rx(
        r"\brecours\b",
        r"\bappeal\b",
        r"\b(a|ont|avez|ai)\s+refus[ée]",
        r"\brefus(é|ée|ait)?\s+(mon|ma|de mon|de ma|le|la)\b",
        r"\b(was|been|got)\s+(refused|rejected|denied)",
        r"\brefusal\b",
        r"tribunal administratif",
    ),
    "expired_permit": _rx(
        r"(expir[ée]+|périm[ée]+)\s+depuis",
        r"\bexpired\b.*\b(ago|since|did not|didn't)",
        r"sans[- ]papiers",
        r"situation irr[ée]guli[èe]re",
        r"\bundocumented\b",
    ),
    "detention": _rx(r"\bcentre de r[ée]tention\b", r"\bdetention\b", r"\bgarde à vue\b"),
}

OUT_OF_SCOPE: dict[str, re.Pattern[str]] = {
    "asylum": _rx(r"\basile\b", r"\basylum\b", r"\br[ée]fugi[ée]", r"\brefugee", r"\bofpra\b"),
    "family_reunification": _rx(
        r"regroupement familial",
        r"family reunification",
        r"r[ée]unification familiale",
        r"(spouse|husband|wife|children)\s+(to\s+)?join\s+me",
        r"faire venir (ma|mon|mes)\s+(femme|mari|époux|épouse|enfants|famille)",
    ),
    "naturalisation": _rx(
        r"naturalis",
        r"nationalit[ée] fran[çc]aise",
        r"french (citizenship|nationality)",
        r"devenir fran[çc]ais",
    ),
}

PERSONAL = _rx(
    r"\bdans mon cas\b",
    r"\bma situation\b",
    r"\bin my (case|situation)\b",
    r"\bque dois-je\b",
    r"\bwhat should i\b",
    r"\bdois-je\b",
    r"\bshould i\b",
)


def classify(question: str) -> ScopeDecision:
    text = unicodedata.normalize("NFC", question)
    personal = bool(PERSONAL.search(text))
    for reason, pattern in HANDOFF.items():
        if pattern.search(text):
            return ScopeDecision("handoff", reason, personal)
    for reason, pattern in OUT_OF_SCOPE.items():
        if pattern.search(text):
            return ScopeDecision("out_of_scope", reason, personal)
    return ScopeDecision("in_scope", None, personal)
