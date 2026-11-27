"""Application settings, loaded from environment variables (see `.env.example`)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Repository-root `.env`, used for local runs outside Docker. Absent inside the container.
ROOT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    """Runtime configuration. Every value comes from the environment or a `.env` file."""

    model_config = SettingsConfigDict(env_file=ROOT_ENV_FILE, extra="ignore")

    database_url: str = Field(
        description="Postgres connection URL. `postgresql://` is normalised to psycopg 3.",
    )
    mistral_api_key: SecretStr | None = None
    piste_client_id: str | None = None
    piste_client_secret: SecretStr | None = None
    daily_question_limit: int = Field(default=20, ge=1)
    monthly_spend_cap_eur: float = Field(default=5.0, gt=0)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str) -> str:
        for prefix in ("postgresql://", "postgres://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value.removeprefix(prefix)
        return value


@lru_cache
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached; call `cache_clear()` in tests)."""
    return Settings()
