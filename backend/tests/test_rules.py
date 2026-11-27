"""Rules table: file schema, grounding checks, matcher, load into Postgres. Fictive values."""

from datetime import UTC, date, datetime
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from db.models import Chunk, Rule
from ingestion.rules import (
    IndexedSource,
    RulesFile,
    check_rules,
    load_rules_file,
    normalise,
    sync_rules,
)
from rag.rules import RuleRow, RulesTool, match, select_rows

SOURCE_TEXT = "Le plafond fictif est de 321 heures par an, soit douze lunes. Il faut payer 42 € ."
NOW = datetime(2026, 11, 27, tzinfo=UTC)


def _rule(**overrides: Any) -> dict[str, Any]:
    rule = {
        "id": "fictive-hours",
        "permit": "cst_etudiant",
        "nationality_scope": "all",
        "topic": "work_limit",
        "stage": "working",
        "label": {"fr": "Plafond fictif", "en": "Fictive cap"},
        "value": "321 heures par an",
        "quote": "Le plafond fictif est de 321 heures par an",
        "source": "fact_sheet:F1",
        "verified_by": None,
        "verified_at": None,
    }
    rule.update(overrides)
    return rule


def _file(*rules: dict[str, Any]) -> RulesFile:
    return RulesFile.model_validate({"version": 1, "rules": list(rules)})


SOURCES = {
    "fact_sheet:F1": IndexedSource(
        normalise(SOURCE_TEXT), "Titre F1", "https://example.test/F1", NOW
    )
}


def test_repository_rules_file_is_valid() -> None:
    rules = load_rules_file()
    assert len(rules.rules) >= 20


def test_a_grounded_rule_passes() -> None:
    assert check_rules(_file(_rule()), SOURCES) == []


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"source": "fact_sheet:F9"}, "not in the index"),
        ({"quote": "Le plafond fictif est de 322 heures par an"}, "quote not found"),
        ({"value": "421 heures par an"}, "value states 421"),
        ({"value": "12 lunes", "quote": "soit douze lunes"}, None),
        ({"value": "42 €", "quote": "Il faut payer 42 € ."}, None),
    ],
)
def test_grounding_checks(overrides: dict[str, Any], error: str | None) -> None:
    errors = check_rules(_file(_rule(**overrides)), SOURCES)
    if error is None:
        assert errors == []
    else:
        assert len(errors) == 1 and error in errors[0]


def test_quote_comparison_ignores_spacing_and_apostrophes() -> None:
    sources = {
        "fact_sheet:F1": IndexedSource(
            normalise("l" + chr(0x2019) + "étudiant" + chr(0x00A0) + "peut   travailler"),
            "T",
            "u",
            NOW,
        )
    }
    rule = _rule(value="travailler", quote="l'étudiant peut travailler")
    assert check_rules(_file(rule), sources) == []


@pytest.mark.parametrize(
    "rules",
    [
        [_rule(), _rule()],  # duplicate id
        [_rule(verified_by="Walid")],  # verified_at missing
        [_rule(topic="weather")],
        [_rule(id="Not Kebab")],
    ],
)
def test_invalid_files_are_rejected(rules: list[dict[str, Any]]) -> None:
    with pytest.raises(ValidationError):
        _file(*rules)


def _row(**overrides: Any) -> RuleRow:
    values: dict[str, Any] = {
        "id": "r",
        "permit": "cst_etudiant",
        "nationality_scope": "all",
        "topic": "work_limit",
        "label_fr": "L",
        "label_en": "L",
        "value": "v",
        "quote": "q",
        "source_type": "fact_sheet",
        "source_id": "F1",
        "source_title": "T",
        "url": "u",
        "source_checked_at": NOW,
        "verified_at": date(2026, 11, 27),
    }
    values.update(overrides)
    return RuleRow(**values)


@pytest.mark.parametrize(
    ("question", "topics", "permits"),
    [
        (
            "Combien d'heures un étudiant peut-il travailler ?",
            {"work_limit"},
            {"cst_etudiant", "cr_etudiant"},
        ),
        ("What is the fee for the multi-year card?", {"fee"}, {"csp_etudiant", "csp_salarie"}),
        ("Quand demander la carte recherche d'emploi ?", {"deadline"}, {"rece"}),
        ("Quelle gratification pour un stage ?", {"internship"}, {"stage_etudiant"}),
    ],
)
def test_match(question: str, topics: set[str], permits: set[str]) -> None:
    found = match(question)
    assert topics <= found.topics
    assert permits <= found.permits


def test_selection_needs_the_topic_the_permit_and_the_scope() -> None:
    rows = [
        _row(id="hours"),
        _row(id="fee", topic="fee"),
        _row(id="rece-hours", permit="rece"),
        _row(id="alg-hours", permit="cr_etudiant", nationality_scope="algerian"),
    ]
    found = match("Combien d'heures un étudiant peut-il travailler ?")
    assert [r.id for r in select_rows(rows, found, algerian=False)] == ["hours"]
    assert [r.id for r in select_rows(rows, found, algerian=True)] == ["alg-hours"]
    assert select_rows(rows, match("Bonjour"), algerian=False) == []


@pytest.mark.db
def test_sync_loads_checked_rows_and_the_tool_reads_only_verified_ones(
    migrated_engine: Engine,
) -> None:
    with Session(migrated_engine) as session, session.begin():
        session.add(
            Chunk(
                id="fact_sheet:F1:0",
                source_type="fact_sheet",
                source_id="F1",
                chunk_index=0,
                title="Titre F1",
                url="https://example.test/F1",
                text=SOURCE_TEXT,
                lang="fr",
                fetched_at=NOW,
                content_hash="h",
            )
        )
    rules = _file(
        _rule(),
        _rule(id="fictive-hours-2", verified_by="Walid", verified_at="2026-11-27"),
    )
    with Session(migrated_engine) as session, session.begin():
        assert sync_rules(session, rules, NOW) == (2, [])
    with Session(migrated_engine) as session:
        stored = session.execute(select(Rule).order_by(Rule.id)).scalars().all()
        assert [(r.id, r.url, r.source_title) for r in stored] == [
            ("fictive-hours", "https://example.test/F1", "Titre F1"),
            ("fictive-hours-2", "https://example.test/F1", "Titre F1"),
        ]
    question = "Combien d'heures un étudiant peut-il travailler ?"
    assert [r.id for r in RulesTool(migrated_engine).lookup(question, False)] == ["fictive-hours-2"]
    drafts = RulesTool(migrated_engine, include_unverified=True).lookup(question, False)
    assert [r.id for r in drafts] == ["fictive-hours", "fictive-hours-2"]
    # A failing row: nothing is written, the previous table stays.
    with Session(migrated_engine) as session, session.begin():
        loaded, errors = sync_rules(session, _file(_rule(value="999 heures")), NOW)
        assert loaded == 0 and errors
    with Session(migrated_engine) as session:
        assert len(session.execute(select(Rule)).scalars().all()) == 2
