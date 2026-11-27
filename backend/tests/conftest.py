import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine

# Importing the app requires a DATABASE_URL; unit tests never connect to it.
os.environ.setdefault("DATABASE_URL", "postgresql://unused:unused@localhost:5432/unused")

from app.config import Settings, get_settings

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
BACKEND_DIR = Path(__file__).resolve().parents[1]


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
