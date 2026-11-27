"""Change planning between stored chunks and freshly loaded drafts (pure, no database).

Per chunk id:
- not stored            -> `added`     (insert, changelog entry)
- stored, same hash     -> metadata refresh only (no changelog entry, embedding kept)
- stored, other hash    -> `modified`  (old row archived, embedding reset, changelog entry)
- stored, not in drafts -> `removed`   (old row archived and deleted, changelog entry)

Only sources listed in `complete_sources` can produce removals, so a source that failed to load
is never emptied.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal

from ingestion.drafts import ChunkDraft

ChangeType = Literal["added", "modified", "removed"]


@dataclass(frozen=True)
class StoredChunk:
    id: str
    source_type: str
    source_id: str
    content_hash: str


@dataclass(frozen=True)
class Change:
    change_type: ChangeType
    chunk_id: str
    source_type: str
    source_id: str
    old_hash: str | None
    new_hash: str | None


@dataclass
class ChangePlan:
    inserts: list[ChunkDraft] = field(default_factory=list)
    updates: list[ChunkDraft] = field(default_factory=list)
    """Content changed: archive the stored row, then overwrite it."""
    refreshes: list[ChunkDraft] = field(default_factory=list)
    """Content unchanged: metadata and `fetched_at` only."""
    deletes: list[StoredChunk] = field(default_factory=list)
    changes: list[Change] = field(default_factory=list)

    @property
    def is_noop(self) -> bool:
        return not self.changes


def plan_changes(
    stored: Iterable[StoredChunk],
    drafts: Sequence[ChunkDraft],
    complete_sources: Iterable[tuple[str, str]],
) -> ChangePlan:
    by_id = {s.id: s for s in stored}
    seen: set[str] = set()
    plan = ChangePlan()
    for draft in drafts:
        if draft.id in seen:
            raise ValueError(f"Duplicate chunk id in drafts: {draft.id}")
        seen.add(draft.id)
        old = by_id.get(draft.id)
        if old is None:
            plan.inserts.append(draft)
            plan.changes.append(_change("added", draft, None))
        elif old.content_hash != draft.content_hash:
            plan.updates.append(draft)
            plan.changes.append(_change("modified", draft, old.content_hash))
        else:
            plan.refreshes.append(draft)

    complete = set(complete_sources)
    for old in by_id.values():
        if old.id not in seen and (old.source_type, old.source_id) in complete:
            plan.deletes.append(old)
            plan.changes.append(
                Change("removed", old.id, old.source_type, old.source_id, old.content_hash, None)
            )
    return plan


def _change(change_type: ChangeType, draft: ChunkDraft, old_hash: str | None) -> Change:
    return Change(
        change_type, draft.id, draft.source_type, draft.source_id, old_hash, draft.content_hash
    )
