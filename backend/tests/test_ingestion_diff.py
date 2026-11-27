"""Change planning (pure). Texts are placeholders, not legal content."""

import pytest

from ingestion.diff import StoredChunk, plan_changes
from ingestion.drafts import ChunkDraft, SourceMeta, chunk_id, content_hash


def _draft(
    index: int, text: str, source_id: str = "F0", stages: tuple[str, ...] = ()
) -> ChunkDraft:
    return ChunkDraft(
        source_type="fact_sheet",
        source_id=source_id,
        chunk_index=index,
        title="Titre",
        url="https://example.test",
        text=text,
        lang="fr",
        meta=SourceMeta(journey_stages=stages),
    )


def _stored(draft: ChunkDraft) -> StoredChunk:
    return StoredChunk(draft.id, draft.source_type, draft.source_id, draft.content_hash)


COMPLETE = [("fact_sheet", "F0")]


def test_chunk_id_and_hash_are_deterministic() -> None:
    assert chunk_id("law", "LEGIARTI000000000001", 2) == "law:LEGIARTI000000000001:2"
    assert content_hash("t", "x") == content_hash("t", "x") != content_hash("t", "y")


def test_first_run_adds_everything() -> None:
    plan = plan_changes([], [_draft(0, "a"), _draft(1, "b")], COMPLETE)
    assert [c.change_type for c in plan.changes] == ["added", "added"]
    assert len(plan.inserts) == 2


def test_rerun_with_same_content_is_noop() -> None:
    drafts = [_draft(0, "a"), _draft(1, "b")]
    plan = plan_changes([_stored(d) for d in drafts], drafts, COMPLETE)
    assert plan.is_noop
    assert len(plan.refreshes) == 2


def test_metadata_change_is_not_a_content_change() -> None:
    old = _draft(0, "a")
    plan = plan_changes([_stored(old)], [_draft(0, "a", stages=("arriving",))], COMPLETE)
    assert plan.is_noop
    assert plan.refreshes[0].meta.journey_stages == ("arriving",)


def test_one_modified_chunk_gives_one_change() -> None:
    old = [_draft(0, "a"), _draft(1, "b")]
    new = [_draft(0, "a"), _draft(1, "b changed")]
    plan = plan_changes([_stored(d) for d in old], new, COMPLETE)
    (change,) = plan.changes
    assert change.change_type == "modified"
    assert (change.old_hash, change.new_hash) == (old[1].content_hash, new[1].content_hash)
    assert [d.id for d in plan.updates] == [new[1].id]


def test_missing_chunk_of_complete_source_is_removed() -> None:
    old = [_draft(0, "a"), _draft(1, "b")]
    plan = plan_changes([_stored(d) for d in old], old[:1], COMPLETE)
    assert [(c.change_type, c.chunk_id) for c in plan.changes] == [("removed", old[1].id)]


def test_incomplete_source_is_never_emptied() -> None:
    stored = [_stored(_draft(0, "a", source_id="F9"))]
    plan = plan_changes(stored, [], complete_sources=[])
    assert plan.is_noop
    assert plan.deletes == []


def test_duplicate_draft_ids_are_rejected() -> None:
    with pytest.raises(ValueError, match="Duplicate chunk id"):
        plan_changes([], [_draft(0, "a"), _draft(0, "b")], COMPLETE)
