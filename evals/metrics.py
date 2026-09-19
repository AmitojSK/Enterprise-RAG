"""Pure retrieval-metric functions: no I/O, so they are unit-tested offline.

A *ranked* list is the retrieved items in rank order, each identified by an
``(filename, page_number)`` pair. *relevant* is the set of pairs a golden item
declares as the expected evidence. Relevance is page-level, matching the
granularity of the app's citations.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

Page = tuple[str, int]


def recall_at_k(ranked: Sequence[Page], relevant: set[Page], k: int) -> float:
    """Fraction of the relevant pages that appear in the top ``k`` results.

    With a single relevant page this reduces to hit@k; with several it rewards
    retrieving more of them. Undefined (0.0) when nothing is relevant.
    """

    if not relevant:
        return 0.0
    found = {p for p in ranked[:k] if p in relevant}
    return len(found) / len(relevant)


def hit_at_k(ranked: Sequence[Page], relevant: set[Page], k: int) -> float:
    """1.0 if any relevant page is in the top ``k``, else 0.0."""

    return 1.0 if any(p in relevant for p in ranked[:k]) else 0.0


def reciprocal_rank(ranked: Sequence[Page], relevant: set[Page]) -> float:
    """1/rank of the first relevant result (0.0 if none). Averaged, this is MRR."""

    for index, page in enumerate(ranked, start=1):
        if page in relevant:
            return 1.0 / index
    return 0.0


def mean(values: Iterable[float | None]) -> float:
    """Mean over the non-``None`` values (0.0 if there are none)."""

    present = [v for v in values if v is not None]
    return sum(present) / len(present) if present else 0.0
