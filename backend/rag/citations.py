"""Citation checker for structured answers (CLAUDE.md, rules 2 and 6).

The `answer` node returns sentences, each with the ids of the retrieved chunks that support it.
A sentence is kept only if:

1. it cites at least one chunk;
2. every cited id is one of the retrieved chunks (no invented ids);
3. every figure it states (amounts, hours, durations, percentages, dates, article numbers)
   appears in the text of at least one cited chunk.

Dropped sentences are reported with the reason; they are never shown. Figures are compared
after normalisation: spaces inside numbers removed (`39 582` = `39582`), decimal comma read as
a point (`877,5` = `877.5`). Legal texts write small numbers in words ("douze mois"): French
number words from zero to ninety-nine in a cited chunk count as figures (`12`), so a faithful
conversion to digits is kept. "un" and "une" read as 1 only before a unit ("un an", "une
semaine"); alone they are the article "a". "neuf" (also "new") reads as 9 only in a compound.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

DropReason = Literal["no_citation", "unknown_chunk", "figure_not_in_source"]

_SPACES = " " + chr(0x00A0) + chr(0x202F)
"""Space, no-break space and narrow no-break space (French thousands separators)."""
_NUMBER = re.compile(rf"\d+(?:[{_SPACES}]\d{{3}})*(?:[.,]\d+)?")


@dataclass(frozen=True)
class AnswerSentence:
    text: str
    chunk_ids: tuple[str, ...]


@dataclass(frozen=True)
class DroppedSentence:
    sentence: AnswerSentence
    reason: DropReason
    detail: str = ""


@dataclass(frozen=True)
class CheckedAnswer:
    kept: list[AnswerSentence]
    dropped: list[DroppedSentence]

    @property
    def cited_chunk_ids(self) -> list[str]:
        seen: dict[str, None] = {}
        for sentence in self.kept:
            seen.update(dict.fromkeys(sentence.chunk_ids))
        return list(seen)


_UNITS = {
    "zéro": 0, "deux": 2, "trois": 3, "quatre": 4, "cinq": 5, "six": 6,
    "sept": 7, "huit": 8, "dix": 10, "onze": 11, "douze": 12, "treize": 13,
    "quatorze": 14, "quinze": 15, "seize": 16, "vingt": 20, "vingts": 20, "trente": 30,
    "quarante": 40, "cinquante": 50, "soixante": 60,
}  # fmt: skip
_IN_COMPOUND = {"un": 1, "une": 1, "neuf": 9}
"""Ambiguous alone (article, adjective); numbers only inside a compound (vingt-et-un, dix-neuf)."""
_WORD = "(?:" + "|".join(sorted(_UNITS, key=len, reverse=True)) + ")"
_TAIL = "(?:" + "|".join(sorted({**_UNITS, **_IN_COMPOUND}, key=len, reverse=True)) + ")"
_WORD_NUMBER = re.compile(rf"(?<![\w-]){_WORD}(?:-(?:et-)?{_TAIL})*(?![\w-])", re.IGNORECASE)

_ONE_UNIT = re.compile(
    r"(?<![\w-])une?\s+(an|ans|année|mois|jour|semaine|heure|fois)(?![\w-])", re.IGNORECASE
)
"""`un an`, `une semaine`: "un" / "une" before a unit is the number 1."""


def word_number(words: str) -> int:
    """`dix-huit` -> 18, `soixante-et-onze` -> 71, `quatre-vingt-dix-neuf` -> 99."""
    total, previous = 0, None
    for token in words.lower().split("-"):
        if token == "et":
            continue
        if token in ("vingt", "vingts") and previous == "quatre":
            total += 80 - 4
        else:
            total += _UNITS.get(token, _IN_COMPOUND.get(token, 0))
        previous = token
    return total


def figures(text: str, number_words: bool = False) -> set[str]:
    """Normalised numbers in `text`; with `number_words`, French number words too."""
    found = {_normalise(m.group(0)) for m in _NUMBER.finditer(text)}
    if number_words:
        found |= {str(word_number(m.group(0))) for m in _WORD_NUMBER.finditer(text)}
        if _ONE_UNIT.search(text):
            found.add("1")
    return found


def _normalise(number: str) -> str:
    compact = re.sub(f"[{_SPACES}]", "", number).replace(",", ".")
    if "." in compact:
        compact = compact.rstrip("0").rstrip(".")
    return compact.lstrip("0") or "0"


def check(sentences: Sequence[AnswerSentence], chunks: Mapping[str, str]) -> CheckedAnswer:
    """`chunks`: retrieved chunk id -> chunk text."""
    kept: list[AnswerSentence] = []
    dropped: list[DroppedSentence] = []
    for sentence in sentences:
        if not sentence.chunk_ids:
            dropped.append(DroppedSentence(sentence, "no_citation"))
            continue
        unknown = [i for i in sentence.chunk_ids if i not in chunks]
        if unknown:
            dropped.append(DroppedSentence(sentence, "unknown_chunk", ", ".join(unknown)))
            continue
        available = set().union(
            *(figures(chunks[i], number_words=True) for i in sentence.chunk_ids)
        )
        missing = sorted(figures(sentence.text) - available)
        if missing:
            dropped.append(DroppedSentence(sentence, "figure_not_in_source", ", ".join(missing)))
            continue
        kept.append(sentence)
    return CheckedAnswer(kept, dropped)
