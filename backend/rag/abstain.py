"""Abstain gate: when the answer is "no official source for this" (CLAUDE.md, rule 2).

Retrieval scores do not separate answerable from unanswerable questions on golden v1 (top
cosine 0.738 to 0.833 for the two abstain items, 0.765 to 0.923 for answer items; every question
has lexical matches), so the gate does not use a similarity threshold. It abstains when:

- retrieval returned nothing;
- the model declared that the retrieved sources do not answer the question;
- no sentence of the answer survived the citation checker (`rag.citations`).
"""

from typing import Literal

from rag.citations import CheckedAnswer

AbstainReason = Literal["no_retrieval", "model_declined", "no_supported_sentence"]


def abstain_reason(
    retrieved: int, model_declined: bool, checked: CheckedAnswer | None
) -> AbstainReason | None:
    if retrieved == 0:
        return "no_retrieval"
    if model_declined:
        return "model_declined"
    if checked is None or not checked.kept:
        return "no_supported_sentence"
    return None
