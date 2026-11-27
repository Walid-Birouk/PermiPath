"""Golden set schema and consistency rules (`evals.golden_set`). Answer fields use obviously
fake values; real expected answers are written by the maintainer."""

from datetime import date
from typing import Any

import pytest
from pydantic import ValidationError

from evals.golden_set import GoldenItem, GoldenSet, load_golden_set, mix, set_path, split_ref

FAKE_FACT = "FAKE FACT for tests"


def _item(**changes: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": "g001",
        "question": "Question de test sur le séjour ?",
        "lang": "fr",
        "stage": "working",
        "expected_behaviour": "answer",
    }
    item.update(changes)
    return item


def _verified(**changes: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "must_cite": ["fact_sheet:F0000"],
        "key_facts": [FAKE_FACT],
        "verified_by": "test",
        "verified_at": "2026-01-01",
    }
    return _item(**{**fields, **changes})


def test_repository_set_is_valid() -> None:
    golden = load_golden_set(set_path("golden"))
    assert golden.version >= 1


def test_draft_item_needs_no_answer_fields() -> None:
    item = GoldenItem.model_validate(_item())
    assert not item.verified


def test_verified_answer_item_needs_sources_and_facts() -> None:
    assert GoldenItem.model_validate(_verified()).verified
    with pytest.raises(ValidationError, match="must_cite and key_facts"):
        GoldenItem.model_validate(_verified(key_facts=[]))


def test_verified_algerian_answer_needs_the_governing_source() -> None:
    with pytest.raises(ValidationError, match="winning_source"):
        GoldenItem.model_validate(_verified(nationality="algerian"))
    item = GoldenItem.model_validate(
        _verified(
            nationality="algerian",
            must_cite=["treaty:accord-test:article-0", "fact_sheet:F0000"],
            winning_source="treaty:accord-test:article-0",
        )
    )
    assert item.winning_source == "treaty:accord-test:article-0"


def test_winning_source_must_be_cited() -> None:
    with pytest.raises(ValidationError, match="one of must_cite"):
        GoldenItem.model_validate(_item(winning_source="law:LEGIARTI000000000000"))


def test_verification_fields_go_together() -> None:
    with pytest.raises(ValidationError, match="both"):
        GoldenItem.model_validate(_item(verified_by="test"))


def test_answerable_question_needs_a_stage_and_out_of_scope_does_not() -> None:
    with pytest.raises(ValidationError, match="journey stage"):
        GoldenItem.model_validate(_item(stage=None))
    assert GoldenItem.model_validate(_item(stage=None, expected_behaviour="refuse"))


@pytest.mark.parametrize("ref", ["F2713", "forum:post-1", "fact_sheet:", "law:with space"])
def test_source_references_are_typed(ref: str) -> None:
    with pytest.raises(ValidationError):
        GoldenItem.model_validate(_item(must_cite=[ref]))


def test_duplicate_ids_and_sources_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate item ids"):
        GoldenSet.model_validate({"version": 1, "items": [_item(), _item()]})
    with pytest.raises(ValidationError, match="duplicate source"):
        GoldenItem.model_validate(_item(must_cite=["fact_sheet:F0000", "fact_sheet:F0000"]))


def test_split_ref_keeps_colons_in_the_source_id() -> None:
    assert split_ref("treaty:accord-franco-algerien-1968:article-7") == (
        "treaty",
        "accord-franco-algerien-1968:article-7",
    )


def test_mix_counts_items() -> None:
    golden = GoldenSet.model_validate(
        {"version": 1, "items": [_verified(), _item(id="g002", lang="en")]}
    )
    counts = mix(golden)
    assert counts["verified"] == {"verified": 1, "draft": 1}
    assert counts["lang"] == {"fr": 1, "en": 1}
    assert GoldenItem.model_validate(_verified()).verified_at == date(2026, 1, 1)
