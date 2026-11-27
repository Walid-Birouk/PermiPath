import pytest

from app.config import Settings


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("postgresql://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
        ("postgres://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
        ("postgresql+psycopg://u:p@h:5432/d", "postgresql+psycopg://u:p@h:5432/d"),
    ],
)
def test_database_url_uses_psycopg_driver(raw: str, expected: str) -> None:
    assert Settings(database_url=raw).database_url == expected


def test_empty_values_count_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """`.env.example` lists optional variables with empty values; copied as is, they must not
    override defaults (an empty secret or endpoint would be used otherwise)."""
    from ingestion.legifrance.settings import PisteSettings

    monkeypatch.setenv("IP_HASH_SECRET", "")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "")
    monkeypatch.setenv("TRACING_MODE", "")
    settings = Settings(_env_file=None, database_url="postgresql://u:p@h/d")
    assert settings.ip_hash_secret is None
    assert settings.langfuse_public_key is None
    assert settings.tracing_mode == "off"
    monkeypatch.setenv("PISTE_OAUTH_URL", "")
    piste = PisteSettings(_env_file=None, client_id="i", client_secret="s")
    assert piste.endpoints().oauth_url.startswith("https://sandbox-oauth.piste.gouv.fr")
