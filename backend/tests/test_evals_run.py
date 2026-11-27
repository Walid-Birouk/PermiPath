"""Eval runs, report comparison and the draft helper (`evals.run_evals`, `evals.compare`,
`evals.draft`), with a fake retriever. Values are fake."""

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from evals.compare import render
from evals.draft import append_item, next_id
from evals.golden_set import GoldenItem, GoldenSet, load_golden_set, set_path
from evals.metrics import Hit
from evals.retrievers import Retrieval
from evals.run_evals import NOT_MEASURED, run, score_item

NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FakeRetriever:
    name = "fake"

    def __init__(self, rankings: dict[str, list[str]]) -> None:
        self._rankings = rankings

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "embedding_model": "fake-embed"}

    def retrieve(self, question: str, depth: int) -> Retrieval:
        refs = self._rankings.get(question, [])[:depth]
        hits = [Hit(f"{ref}:0", ref, 1.0 - i / 10) for i, ref in enumerate(refs)]
        return Retrieval(hits=hits, embedding_tokens=2)


def _golden() -> GoldenSet:
    verified = {"verified_by": "test", "verified_at": "2026-01-01", "key_facts": ["FAKE FACT"]}
    return GoldenSet.model_validate(
        {
            "version": 1,
            "items": [
                {
                    "id": "g001",
                    "question": "Question algérienne de test ?",
                    "lang": "fr",
                    "stage": "renewing",
                    "nationality": "algerian",
                    "expected_behaviour": "answer",
                    "must_cite": ["treaty:T:article-0", "fact_sheet:F0"],
                    "winning_source": "treaty:T:article-0",
                    **verified,
                },
                {
                    "id": "g002",
                    "question": "Out of scope test question?",
                    "lang": "en",
                    "expected_behaviour": "refuse",
                    "verified_by": "test",
                    "verified_at": "2026-01-01",
                },
                {
                    "id": "g003",
                    "question": "Question brouillon de test ?",
                    "lang": "fr",
                    "stage": "working",
                    "expected_behaviour": "answer",
                },
            ],
        }
    )


RANKINGS = {
    "Question algérienne de test ?": ["fact_sheet:F0", "fact_sheet:F9", "treaty:T:article-0"],
    "Out of scope test question?": ["fact_sheet:F9"],
}


def test_score_item_measures_recall_rank_and_precedence() -> None:
    item = _golden().items[0]
    ticks = iter([1.0, 1.25])
    result = score_item(item, FakeRetriever(RANKINGS), depth=20, clock=lambda: next(ticks))
    assert result["scored"] is True
    assert result["ranking"][:3] == RANKINGS[item.question]
    assert (result["first_rank"], result["reciprocal_rank"]) == (1, 1.0)
    assert result["recall@5"] == 1.0
    assert result["right_source_wins"] is False  # the sheet outranks the governing treaty
    assert (result["latency_ms"], result["embedding_tokens"]) == (250.0, 2)
    assert result["top_score"] == pytest.approx(1.0)


def test_run_scores_verified_items_only_by_default() -> None:
    report = run(_golden(), "golden", FakeRetriever(RANKINGS), now=NOW)
    assert [i["id"] for i in report["items"]] == ["g001", "g002"]
    assert report["set"] == {
        "name": "golden",
        "version": 1,
        "items": 2,
        "verified": 2,
        "includes_drafts": False,
    }
    summary = report["summary"]
    assert (summary["items"], summary["items_scored"], summary["precedence_items"]) == (2, 1, 1)
    assert summary["recall@5"] == 1.0
    assert summary["right_source_wins"] == 0.0
    assert summary["embedding_tokens"] == 4
    assert summary["not_measured"] == list(NOT_MEASURED)
    refused = report["items"][1]
    assert refused["scored"] is False and refused["recall@5"] is None
    assert report["pipeline"]["depth"] == 20


def test_run_with_drafts_flags_the_report() -> None:
    report = run(_golden(), "golden", FakeRetriever(RANKINGS), include_drafts=True, now=NOW)
    assert report["set"]["includes_drafts"] is True
    assert report["items"][2]["scored"] is False  # draft without must_cite


def test_compare_renders_metric_deltas_and_rank_changes() -> None:
    base = run(_golden(), "golden", FakeRetriever(RANKINGS), now=NOW)
    better = {**RANKINGS, "Question algérienne de test ?": ["treaty:T:article-0", "fact_sheet:F0"]}
    candidate = run(_golden(), "golden", FakeRetriever(better), now=NOW)
    table = render(base, candidate)
    assert "| Right source wins | 0.000 | 1.000 | +1.000 |" in table
    assert "| g001 | 1 | 1 |" not in table  # first relevant rank unchanged
    assert "Not comparable" not in table
    assert "`answer_correctness`" in table


def test_compare_flags_reports_on_different_items() -> None:
    base = run(_golden(), "golden", FakeRetriever(RANKINGS), now=NOW)
    other = run(_golden(), "golden", FakeRetriever(RANKINGS), include_drafts=True, now=NOW)
    table = render(base, other)
    assert "Not comparable" in table
    assert "draft" in table


def test_draft_helper_appends_a_valid_item_and_keeps_comments(tmp_path: Path) -> None:
    path = tmp_path / "golden.yaml"
    shutil.copy(set_path("golden"), path)
    golden = load_golden_set(path)
    item = GoldenItem(
        id=next_id(golden),
        question='Peut-on travailler "à temps partiel" : oui ou non ?',
        lang="fr",
        stage="working",
        expected_behaviour="answer",
        origin="test",
    )
    append_item(path, item)
    reloaded = load_golden_set(path)
    assert reloaded.items[-1] == item
    assert "# Golden question set" in path.read_text(encoding="utf-8")
    assert next_id(reloaded) == f"g{len(golden.items) + 2:03d}"
    assert yaml.safe_load(path.read_text(encoding="utf-8"))["items"][-1]["must_cite"] == []
