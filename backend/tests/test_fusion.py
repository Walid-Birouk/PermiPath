import pytest

from rag.fusion import rrf


def test_documents_in_both_rankings_rise_above_single_ranking_leaders() -> None:
    fused = rrf({"lexical": ["a", "b", "c"], "vector": ["d", "b", "a"]}, k=60)
    assert [doc for doc, _ in fused][:2] == ["a", "b"]
    assert dict(fused)["a"] == pytest.approx(1 / 61 + 1 / 63)


def test_weights_scale_a_ranking_and_zero_removes_its_influence() -> None:
    rankings = {"lexical": ["x", "y"], "vector": ["y", "x"]}
    assert rrf(rankings, weights={"lexical": 2.0, "vector": 1.0})[0][0] == "x"
    only_vector = rrf(rankings, weights={"lexical": 0.0, "vector": 1.0})
    assert [d for d, _ in only_vector] == ["y", "x"]
    # A zero-weight ranking adds no documents of its own.
    sparse = rrf({"lexical": ["only-lexical"], "vector": ["v"]}, weights={"lexical": 0.0})
    assert [d for d, _ in sparse] == ["v"]
    assert sparse[0][1] == pytest.approx(1 / 61)


def test_ties_are_broken_by_id_and_empty_rankings_are_allowed() -> None:
    assert [d for d, _ in rrf({"a": ["z"], "b": ["m"]})] == ["m", "z"]
    assert rrf({"lexical": [], "vector": []}) == []


def test_invalid_input_is_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        rrf({"lexical": ["a", "a"]})
    with pytest.raises(ValueError, match="k must"):
        rrf({"lexical": ["a"]}, k=-1)
    with pytest.raises(ValueError, match="negative"):
        rrf({"lexical": ["a"]}, weights={"lexical": -1.0})
