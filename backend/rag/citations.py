"""Citation checker for structured answers (CLAUDE.md, rules 2 and 6).

The `answer` node returns sentences, each with the ids of the retrieved chunks that support it.
A sentence is kept only if:

1. it cites at least one chunk;
2. every cited id is one of the retrieved chunks (no invented ids);
3. every figure it states (amounts, hours, durations, percentages, dates, article numbers)
   appears in the text of at least one cited chunk.

Dropped sentences are reported with the reason; they are never shown. Figures are compared
after normalisation: spaces inside numbers removed (`39 582` = `39582`), decimal comma read as
a point (`877,5` = `877.5`).
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


def figures(text: str) -> set[str]:
    """Normalised numbers in `text`."""
    return {_normalise(m.group(0)) for m in _NUMBER.finditer(text)}


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
        available = set().union(*(figures(chunks[i]) for i in sentence.chunk_ids))
        missing = sorted(figures(sentence.text) - available)
        if missing:
            dropped.append(DroppedSentence(sentence, "figure_not_in_source", ", ".join(missing)))
            continue
        kept.append(sentence)
    return CheckedAnswer(kept, dropped)
