"""Reciprocal Rank Fusion (Cormack, Clarke and Büttcher, SIGIR 2009).

score(d) = sum over rankings r of weight_r / (k + rank_r(d)), ranks starting at 1. Documents
absent from a ranking contribute nothing for it. Ties are broken by document id so the fused
order is deterministic.
"""

from collections.abc import Mapping, Sequence

DEFAULT_K = 60
"""Constant from the original paper; damps the weight of the very first ranks."""


def rrf(
    rankings: Mapping[str, Sequence[str]],
    k: int = DEFAULT_K,
    weights: Mapping[str, float] | None = None,
) -> list[tuple[str, float]]:
    """Fuses named rankings (best first, ids unique within a ranking) into `(id, score)` pairs,
    best first. A ranking with weight 0 is ignored: its documents are not added."""
    if k < 0:
        raise ValueError("k must be >= 0")
    scores: dict[str, float] = {}
    for name, ranking in rankings.items():
        weight = 1.0 if weights is None else weights.get(name, 1.0)
        if weight < 0:
            raise ValueError(f"ranking {name!r} has a negative weight")
        if len(set(ranking)) != len(ranking):
            raise ValueError(f"ranking {name!r} has duplicate ids")
        if weight == 0:
            continue
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + weight / (k + rank)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))
