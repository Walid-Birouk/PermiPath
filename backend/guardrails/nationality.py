"""Detects whether a question is about an Algerian national (D-009 precedence).

Only an explicit mention counts ("algérien", "Algerian", "Algérie", "certificat de résidence",
which only Algerians receive). Anything else is `unknown`: the `clarify` node asks, it does not
guess. The text is not stored (CLAUDE.md, rule 5).
"""

import re
import unicodedata
from typing import Literal

Nationality = Literal["algerian", "unknown"]

_ALGERIAN = re.compile(
    r"\b(alg[eé]rien(ne)?s?|algerian?s?|alg[eé]rie|algeria|certificats? de r[eé]sidence"
    r"|residence certificates?)\b",
    re.IGNORECASE,
)


def detect_nationality(question: str) -> Nationality:
    text = unicodedata.normalize("NFC", question)
    return "algerian" if _ALGERIAN.search(text) else "unknown"
