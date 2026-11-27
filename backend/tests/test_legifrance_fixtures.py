"""Légifrance client replayed against real API responses recorded from the PISTE sandbox.

Fixtures: `tests/fixtures/legifrance/` (Légifrance / DILA, Licence Ouverte 2.0). Only the OAuth
token response is synthetic. Edge cases (version fallback, 401 retry, quota) stay in
`test_legifrance_client.py`.
"""

import json
from datetime import date
from pathlib import Path
from typing import Any

import httpx2
import pytest

from ingestion.legifrance import check
from ingestion.legifrance.client import CESEDA_TITLE, LegifranceClient
from ingestion.legifrance.settings import PisteEndpoints

FIXTURES = Path(__file__).parent / "fixtures" / "legifrance"
ENDPOINTS = PisteEndpoints(oauth_url="https://oauth.test/token", api_url="https://api.test/lf")
CLIENT_ID, CLIENT_SECRET = "test-client-id", "test-client-secret"
ON = date(2026, 11, 26)

# Identifiers and dates as they appear in the recorded responses.
CESEDA_ID = "LEGITEXT000006070158"
L422_1_ID = "LEGIARTI000042776689"
L422_1_IN_FORCE_FROM = date(2021, 5, 1)


def _fixture(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data


def _replay(request: httpx2.Request) -> httpx2.Response:
    if request.url == ENDPOINTS.oauth_url:
        return httpx2.Response(200, json={"access_token": "tok-replay", "expires_in": 3600})
    path = request.url.path.removeprefix("/lf/")
    try:
        return httpx2.Response(200, json=_fixture(f"{path.replace('/', '_')}.json"))
    except FileNotFoundError:
        return httpx2.Response(404, text="no fixture")


@pytest.fixture
def client() -> LegifranceClient:
    http = httpx2.Client(transport=httpx2.MockTransport(_replay))
    return LegifranceClient(ENDPOINTS, CLIENT_ID, CLIENT_SECRET, http=http)


def test_find_code_locates_ceseda(client: LegifranceClient) -> None:
    code = client.find_code()
    assert code.id == CESEDA_ID
    assert code.title == CESEDA_TITLE
    assert code.status == "VIGUEUR"


def test_find_article_id_reads_search_extracts(client: LegifranceClient) -> None:
    assert client.find_article_id("L422-1", ON) == L422_1_ID
    assert client.find_article_id("L. 422-1", ON) == L422_1_ID


def test_get_article_parses_epoch_dates_and_versions(client: LegifranceClient) -> None:
    article, versions = client.get_article(L422_1_ID)
    assert (article.id, article.num, article.status) == (L422_1_ID, "L422-1", "VIGUEUR")
    assert article.in_force_from == L422_1_IN_FORCE_FROM
    assert article.in_force_to is None  # 2999-01-01 sentinel
    assert article.text.startswith("L'étranger qui établit")
    assert "<" not in article.text
    assert [v["id"] for v in versions] == [L422_1_ID]


def test_article_in_force_on_date(client: LegifranceClient) -> None:
    article = client.article_in_force("L422-1", ON)
    assert article.id == L422_1_ID
    assert article.in_force_on(ON)
    assert not article.in_force_on(date(2021, 4, 30))
    assert article.url(ON).endswith(f"/codes/article_lc/{L422_1_ID}/2026-11-26")


def test_cli_against_recorded_responses(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PISTE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("PISTE_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("PISTE_OAUTH_URL", ENDPOINTS.oauth_url)
    monkeypatch.setenv("PISTE_API_URL", ENDPOINTS.api_url)
    http = httpx2.Client(transport=httpx2.MockTransport(_replay))

    code = check.main(["--article", "L422-1", "--date", ON.isoformat()], env_file=None, http=http)

    out = capsys.readouterr().out
    assert code == check.EXIT_OK
    assert CESEDA_ID in out
    assert L422_1_ID in out
    assert "2021-05-01 -> no end date" in out
    assert CLIENT_SECRET not in out
    assert "tok-replay" not in out
