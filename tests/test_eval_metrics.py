"""Unit tests for the pure retrieval-metric functions (no network, no cost)."""

from evals.metrics import hit_at_k, mean, recall_at_k, reciprocal_rank

DOC = "cloud-cost-guide.pdf"
RANKED = [(DOC, 3), (DOC, 1), (DOC, 3), (DOC, 5)]  # page 3 appears at ranks 1 and 3


def test_hit_at_k():
    assert hit_at_k(RANKED, {(DOC, 3)}, 1) == 1.0
    assert hit_at_k(RANKED, {(DOC, 5)}, 1) == 0.0  # page 5 is at rank 4
    assert hit_at_k(RANKED, {(DOC, 5)}, 4) == 1.0


def test_recall_at_k_single_relevant():
    assert recall_at_k(RANKED, {(DOC, 1)}, 2) == 1.0  # page 1 is at rank 2
    assert recall_at_k(RANKED, {(DOC, 1)}, 1) == 0.0


def test_recall_at_k_multi_relevant():
    relevant = {(DOC, 1), (DOC, 5)}
    assert recall_at_k(RANKED, relevant, 2) == 0.5  # only page 1 in top 2
    assert recall_at_k(RANKED, relevant, 4) == 1.0  # both by rank 4


def test_recall_empty_relevant_is_zero():
    assert recall_at_k(RANKED, set(), 5) == 0.0


def test_reciprocal_rank():
    assert reciprocal_rank(RANKED, {(DOC, 3)}) == 1.0        # first result
    assert reciprocal_rank(RANKED, {(DOC, 1)}) == 0.5        # second result
    assert reciprocal_rank(RANKED, {(DOC, 9)}) == 0.0        # absent


def test_mean_ignores_none():
    assert mean([1.0, None, 0.0]) == 0.5
    assert mean([None]) == 0.0
    assert mean([]) == 0.0
