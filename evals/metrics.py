"""Retrieval metrics over a ranked list of retrieved chunks, at source level.

A source is cited when any of its chunks is retrieved, so chunk hits are first reduced to the
ordered list of distinct sources. Ranks are 1-based. A metric that does not apply to an item
(e.g. recall for a question with no expected source) is `None` and is left out of the means.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from statistics import mean


@dataclass(frozen=True)
class Hit:
    chunk_id: str
    source_ref: str
    """`{source_type}:{source_id}` of the chunk."""
    score: float
    """Similarity (higher is closer)."""


def source_ranking(hits: Iterable[Hit]) -> list[str]:
    """Distinct source references in order of first appearance."""
    seen: dict[str, None] = {}
    for hit in hits:
        seen.setdefault(hit.source_ref, None)
    return list(seen)


def recall_at_k(ranking: Sequence[str], expected: Sequence[str], k: int) -> float | None:
    """Share of the expected sources found in the top `k` sources."""
    if not expected:
        return None
    top = set(ranking[:k])
    return sum(1 for ref in expected if ref in top) / len(expected)


def first_rank(ranking: Sequence[str], expected: Sequence[str]) -> int | None:
    """Rank of the first expected source retrieved, or None if none is retrieved."""
    wanted = set(expected)
    return next((i for i, ref in enumerate(ranking, start=1) if ref in wanted), None)


def reciprocal_rank(ranking: Sequence[str], expected: Sequence[str]) -> float | None:
    if not expected:
        return None
    rank = first_rank(ranking, expected)
    return 0.0 if rank is None else 1.0 / rank


def right_source_wins(
    ranking: Sequence[str], expected: Sequence[str], winning: str | None
) -> bool | None:
    """True when the governing source is retrieved and ranks above every other expected
    source (precedence cases, D-009); None when the item has no governing source."""
    if winning is None:
        return None
    winner_rank = first_rank(ranking, [winning])
    return winner_rank is not None and winner_rank == first_rank(ranking, expected)


def mean_of(values: Iterable[float | bool | None]) -> float | None:
    """Mean of the applicable values (booleans count as 0/1); None when none applies."""
    present = [float(v) for v in values if v is not None]
    return mean(present) if present else None


def percentile(values: Sequence[float], q: float) -> float | None:
    """Nearest-rank percentile (`q` in 0..100)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]
