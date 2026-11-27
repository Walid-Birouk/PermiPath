"""Applies the migrations to a disposable database and checks the resulting schema.

Destructive: runs `downgrade base`. Only runs when TEST_DATABASE_URL is set.
"""

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, inspect, text

from app.config import Settings, get_settings

pytestmark = pytest.mark.db

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_DIR = Path(__file__).resolve().parents[1]
EXPECTED_TABLES = {"chunks", "chunk_versions", "changelog", "query_logs"}


@pytest.fixture
def migrated_engine(monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    get_settings.cache_clear()
    url = Settings(database_url=TEST_DATABASE_URL).database_url
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    try:
        yield engine
    finally:
        engine.dispose()
        command.downgrade(cfg, "base")
        get_settings.cache_clear()


def test_schema_has_expected_tables(migrated_engine: Engine) -> None:
    assert EXPECTED_TABLES.issubset(inspect(migrated_engine).get_table_names())


def test_query_logs_has_no_free_text_columns(migrated_engine: Engine) -> None:
    columns = {c["name"] for c in inspect(migrated_engine).get_columns("query_logs")}
    assert columns == {
        "id",
        "created_at",
        "journey_stage",
        "retrieved_chunk_ids",
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "eval_flags",
    }


def test_chunk_tsv_is_generated_with_language_config(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chunks (id, source_type, source_id, chunk_index, title, url, text,"
                " lang, journey_stages, fetched_at, content_hash)"
                " VALUES ('t:1:0', 'sp_sheet', 'F0000', 0, 'Titre', 'https://example.test',"
                " 'Les étudiants renouvellent leurs titres', 'fr', ARRAY['renewing'], :now, 'h')"
            ),
            {"now": datetime.now(UTC)},
        )
        matched = conn.execute(
            text("SELECT count(*) FROM chunks WHERE tsv @@ plainto_tsquery('french', 'étudiant')")
        ).scalar_one()
    assert matched == 1


def test_chunk_rejects_unknown_journey_stage(migrated_engine: Engine) -> None:
    with pytest.raises(Exception, match="ck_chunks_stages"), migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chunks (id, source_type, source_id, chunk_index, title, url, text,"
                " lang, journey_stages, fetched_at, content_hash)"
                " VALUES ('t:1:1', 'sp_sheet', 'F0000', 1, 'T', 'u', 'x', 'fr',"
                " ARRAY['not_a_stage'], now(), 'h')"
            )
        )
