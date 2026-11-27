"""PISTE / Légifrance configuration, read from the environment or the repository `.env`.

Kept separate from `app.config.Settings` so the Légifrance tools run without a database URL.
"""

from dataclasses import dataclass
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.config import ROOT_ENV_FILE


@dataclass(frozen=True)
class PisteEndpoints:
    oauth_url: str
    api_url: str


# Sandbox: OAuth URL and API host are from Walid's PISTE app page; the API host is also the
# "URL de base" on data.gouv.fr/dataservices/legifrance. The base path
# `/dila/legifrance/lf-engine-app` (from the pylegifrance client, v1.7.2) was confirmed by a live
# sandbox run on 2026-11-27.
SANDBOX = PisteEndpoints(
    oauth_url="https://sandbox-oauth.piste.gouv.fr/api/oauth/token",
    api_url="https://sandbox-api.piste.gouv.fr/dila/legifrance/lf-engine-app",
)
# PROD: UNVERIFIED, taken from pylegifrance v1.7.2 defaults. Check on PISTE before deploying.
PROD = PisteEndpoints(
    oauth_url="https://oauth.piste.gouv.fr/api/oauth/token",
    api_url="https://api.piste.gouv.fr/dila/legifrance/lf-engine-app",
)


class PisteSettings(BaseSettings):
    """`PISTE_*` variables. `oauth_url` / `api_url` override the per-environment defaults."""

    model_config = SettingsConfigDict(env_prefix="PISTE_", env_file=ROOT_ENV_FILE, extra="ignore")

    client_id: str
    client_secret: SecretStr
    env: Literal["sandbox", "prod"] = "sandbox"
    oauth_url: str | None = None
    api_url: str | None = None

    def endpoints(self) -> PisteEndpoints:
        default = SANDBOX if self.env == "sandbox" else PROD
        return PisteEndpoints(
            oauth_url=self.oauth_url or default.oauth_url,
            api_url=(self.api_url or default.api_url).rstrip("/"),
        )
