"""Retrieval metrics (`evals.metrics`)."""

import pytest

from evals.metrics import (
    Hit,
    first_rank,
    mean_of,
    percentile,
    recall_at_k,
    reciprocal_rank,
    right_source_wins,
    source_ranking,
)

RANKING = ["fact_sheet:A", "law:B", "treaty:C", "fact_sheet:D"]


def test_source_ranking_keeps_first_appearance_of_each_source() -> None:
    hits = [Hit("a0", "fact_sheet:A", 0.9), Hit("b0", "law:B", 0.8), Hit("a1", "fact_sheet:A", 0.7)]
    assert source_ranking(hits) == ["fact_sheet:A", "law:B"]


def test_recall_at_k() -> None:
    assert recall_at_k(RANKING, ["law:B", "fact_sheet:D"], 2) == 0.5
    assert recall_at_k(RANKING, ["law:B", "fact_sheet:D"], 4) == 1.0
    assert recall_at_k(RANKING, [], 5) is None


def test_first_and_reciprocal_rank() -> None:
    assert first_rank(RANKING, ["treaty:C", "fact_sheet:D"]) == 3
    assert reciprocal_rank(RANKING, ["treaty:C"]) == pytest.approx(1 / 3)
    assert reciprocal_rank(RANKING, ["law:Z"]) == 0.0
    assert first_rank(RANKING, ["law:Z"]) is None
    assert reciprocal_rank(RANKING, []) is None


def test_right_source_wins_when_governing_source_ranks_first_among_expected() -> None:
    expected = ["treaty:C", "fact_sheet:D"]
    assert right_source_wins(RANKING, expected, "treaty:C") is True
    assert right_source_wins(RANKING, expected, "fact_sheet:D") is False
    assert right_source_wins(RANKING, ["law:Z"], "law:Z") is False
    assert right_source_wins(RANKING, expected, None) is None


def test_mean_of_skips_values_that_do_not_apply() -> None:
    assert mean_of([1.0, None, 0.0]) == 0.5
    assert mean_of([True, False, None, True]) == pytest.approx(2 / 3)
    assert mean_of([None]) is None


def test_nearest_rank_percentile() -> None:
    values = [float(v) for v in range(1, 11)]
    assert percentile(values, 50) == 5.0
    assert percentile(values, 95) == 10.0
    assert percentile([7.0], 95) == 7.0
    assert percentile([], 50) is None
