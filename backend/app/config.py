"""Application settings, loaded from environment variables (see `.env.example`)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

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
    """Questions per visitor per day (visitor = salted hash of the client IP, D-020)."""
    monthly_spend_cap_eur: float = Field(default=5.0, gt=0)
    """`/ask` pauses for the rest of the month once the estimated spend reaches this."""
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
    ip_hash_secret: SecretStr | None = None
    """Secret for the daily visitor hash. Unset: a random secret per process (counts reset on
    restart); set it in production."""
    trust_forwarded_for: bool = False
    """Read the client IP from the first `X-Forwarded-For` entry (behind the host's proxy)."""
    # Mistral list prices, USD per million tokens (docs.mistral.ai/inference/pricing and the
    # model cards, read 2026-11-27). Counted 1:1 as EUR by the spend cap (D-020).
    price_chat_input_per_m: float = Field(default=0.15, ge=0)
    price_chat_output_per_m: float = Field(default=0.6, ge=0)
    price_embed_input_per_m: float = Field(default=0.1, ge=0)
    tracing_mode: Literal["off", "full", "metadata"] = "off"
    """Langfuse tracing (D-021): `full` in development and evals, `metadata` (no text) in
    production."""
    langfuse_public_key: str | None = None
    langfuse_secret_key: SecretStr | None = None
    langfuse_base_url: str = "https://cloud.langfuse.com"
    """EU region by default."""
    langfuse_environment: str = "development"

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
