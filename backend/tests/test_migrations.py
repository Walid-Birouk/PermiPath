"""Applies the migrations to a disposable database and checks the resulting schema.

Destructive: runs `downgrade base`. Only runs when TEST_DATABASE_URL is set.
"""

import os
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
                " VALUES ('t:1:0', 'fact_sheet', 'F0000', 0, 'Titre', 'https://example.test',"
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
                " VALUES ('t:1:1', 'fact_sheet', 'F0000', 1, 'T', 'u', 'x', 'fr',"
                " ARRAY['not_a_stage'], now(), 'h')"
            )
        )


def test_chunk_metadata_defaults_to_official_and_all(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chunks (id, source_type, source_id, chunk_index, title, url, text,"
                " lang, fetched_at, content_hash)"
                " VALUES ('t:2:0', 'law', 'LEGIARTI000000000001', 0, 'T', 'u', 'x', 'fr',"
                " now(), 'h')"
            )
        )
        row = conn.execute(
            text("SELECT authority, nationality_scope, prefecture FROM chunks WHERE id = 't:2:0'")
        ).one()
    assert tuple(row) == ("official", "all", None)


@pytest.mark.parametrize(
    ("column", "value", "constraint"),
    [
        ("source_type", "sp_sheet", "ck_chunks_source_type"),
        ("authority", "forum", "ck_chunks_authority"),
        ("nationality_scope", "tunisian", "ck_chunks_nationality_scope"),
    ],
)
def test_chunk_rejects_unknown_metadata(
    migrated_engine: Engine, column: str, value: str, constraint: str
) -> None:
    values = {"source_type": "law", "authority": "official", "nationality_scope": "all"}
    values[column] = value
    with pytest.raises(Exception, match=constraint), migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chunks (id, source_type, authority, nationality_scope, source_id,"
                " chunk_index, title, url, text, lang, fetched_at, content_hash)"
                " VALUES ('t:3:0', :source_type, :authority, :nationality_scope, 'S', 0, 'T',"
                " 'u', 'x', 'fr', now(), 'h')"
            ),
            values,
        )


def test_upgrade_remaps_existing_source_types(monkeypatch: pytest.MonkeyPatch) -> None:
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    monkeypatch.setenv("DATABASE_URL", TEST_DATABASE_URL)
    get_settings.cache_clear()
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    engine = create_engine(Settings(database_url=TEST_DATABASE_URL).database_url)
    insert = text(
        "INSERT INTO chunks (id, source_type, source_id, chunk_index, title, url, text, lang,"
        " fetched_at, content_hash) VALUES (:id, :st, :id, 0, 'T', 'u', 'x', 'fr', now(), 'h')"
    )
    try:
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "0001")
        with engine.begin() as conn:
            conn.execute(
                insert, [{"id": "a", "st": "ceseda_article"}, {"id": "b", "st": "sp_sheet"}]
            )
        command.upgrade(cfg, "0002")
        with engine.begin() as conn:
            result = conn.execute(text("SELECT id, source_type FROM chunks")).all()
        rows = {str(row[0]): str(row[1]) for row in result}
        assert rows == {"a": "law", "b": "fact_sheet"}
    finally:
        engine.dispose()
        command.downgrade(cfg, "base")
        get_settings.cache_clear()


def test_settling_stage_is_accepted(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chunks (id, source_type, source_id, chunk_index, title, url, text,"
                " lang, journey_stages, fetched_at, content_hash)"
                " VALUES ('t:4:0', 'fact_sheet', 'F0000', 0, 'T', 'u', 'x', 'fr',"
                " ARRAY['job_offer', 'settling'], now(), 'h')"
            )
        )
        conn.execute(text("INSERT INTO query_logs (journey_stage) VALUES ('settling')"))


def test_case_law_source_type_is_accepted(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chunks (id, source_type, source_id, chunk_index, title, url, text,"
                " lang, journey_stages, fetched_at, content_hash)"
                " VALUES ('case_law:CETATEXT0:0', 'case_law', 'CETATEXT0', 0, 'T', 'u', 'x',"
                " 'fr', ARRAY['finishing'], now(), 'h')"
            )
        )


def test_every_table_has_row_level_security(migrated_engine: Engine) -> None:
    """No table may be reachable through the Supabase Data API (D-022): a new table must be
    created with RLS enabled (and its client-role grants revoked on Supabase)."""
    with migrated_engine.connect() as conn:
        without_rls = (
            conn.execute(
                text(
                    "SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE n.nspname = 'public' AND c.relkind = 'r' AND NOT c.relrowsecurity"
                )
            )
            .scalars()
            .all()
        )
    assert without_rls == []
