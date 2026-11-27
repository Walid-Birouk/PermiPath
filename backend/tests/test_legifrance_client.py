"""Légifrance client tests. All HTTP is mocked; response shapes mirror the Légifrance Swagger
(as reproduced by pylegifrance). Article texts are placeholders, not legal content."""

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx2
import pytest

from ingestion.legifrance import check
from ingestion.legifrance.client import (
    CESEDA_TITLE,
    LegifranceApiError,
    LegifranceClient,
    NotFoundError,
    PisteAuthError,
    _parse_date,
    normalise_article_num,
)
from ingestion.legifrance.settings import PROD, SANDBOX, PisteEndpoints, PisteSettings

ENDPOINTS = PisteEndpoints(oauth_url="https://oauth.test/token", api_url="https://api.test/lf")
CLIENT_ID, CLIENT_SECRET = "test-client-id", "test-client-secret"
CODE_ID = "LEGITEXT000006070158"
OLD_ID, CURRENT_ID = "LEGIARTI000000000001", "LEGIARTI000000000002"
MS_2021_05_01 = 1619827200000
MS_2999_01_01 = 32472144000000
MS_2019_01_01 = 1546300800000
CESEDA_CURLY = CESEDA_TITLE.replace("'", "\N{RIGHT SINGLE QUOTATION MARK}")


def _article(article_id: str, start_ms: int, end_ms: int, text: str) -> dict[str, Any]:
    versions = [
        {"id": OLD_ID, "dateDebut": MS_2019_01_01, "dateFin": MS_2021_05_01, "etat": "MODIFIE"},
        {"id": CURRENT_ID, "dateDebut": MS_2021_05_01, "dateFin": MS_2999_01_01, "etat": "VIGUEUR"},
    ]
    return {
        "article": {
            "id": article_id,
            "num": "L422-1",
            "etat": "VIGUEUR" if article_id == CURRENT_ID else "MODIFIE",
            "dateDebut": start_ms,
            "dateFin": end_ms,
            "texte": text,
            "articleVersions": versions,
        }
    }


ARTICLES = {
    OLD_ID: _article(OLD_ID, MS_2019_01_01, MS_2021_05_01, "Texte de test (ancienne version)."),
    CURRENT_ID: _article(CURRENT_ID, MS_2021_05_01, MS_2999_01_01, "Texte de test (en vigueur)."),
}


class FakePiste:
    """Mock PISTE: OAuth + the three API endpoints. Records every request."""

    def __init__(self, search_hit_id: str = CURRENT_ID) -> None:
        self.requests: list[httpx2.Request] = []
        self.search_hit_id = search_hit_id
        self.tokens_issued = 0
        self.reject_next_api_call = False

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if request.url == ENDPOINTS.oauth_url:
            self.tokens_issued += 1
            return httpx2.Response(
                200, json={"access_token": f"tok-{self.tokens_issued}", "expires_in": 3600}
            )
        if self.reject_next_api_call:
            self.reject_next_api_call = False
            return httpx2.Response(401, json={"error": "invalid_token"})
        path = request.url.path.removeprefix("/lf/")
        body = json.loads(request.content)
        if path == "list/code":
            return httpx2.Response(200, json={"results": [
                {"id": "LEGITEXT000000000999", "titre": "Code civil", "etat": "VIGUEUR"},
                {"id": CODE_ID, "titre": CESEDA_CURLY, "etat": "VIGUEUR"},
            ]})  # fmt: skip
        if path == "search":
            return httpx2.Response(200, json={"results": [{"sections": [{"extracts": [
                {"type": "section", "id": "LEGISCTA000000000001"},
                {"type": "articles", "id": self.search_hit_id, "num": "L422-1"},
            ]}]}]})  # fmt: skip
        if path == "consult/getArticle":
            return httpx2.Response(200, json=ARTICLES[body["id"]])
        return httpx2.Response(404, text="unknown path")

    def bodies(self, path: str) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.requests if r.url.path == f"/lf/{path}"]


def _client(fake: FakePiste, clock: Callable[[], float] = lambda: 0.0) -> LegifranceClient:
    http = httpx2.Client(transport=httpx2.MockTransport(fake))
    return LegifranceClient(ENDPOINTS, CLIENT_ID, CLIENT_SECRET, http=http, clock=clock)


# --- auth -----------------------------------------------------------------------------------


def test_token_request_uses_client_credentials_form() -> None:
    fake = FakePiste()
    _client(fake).access_token()
    sent = fake.requests[0]
    assert sent.method == "POST"
    form = dict(httpx2.QueryParams(sent.content.decode()))
    assert form == {
        "grant_type": "client_credentials",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "scope": "openid",
    }


def test_token_is_cached_then_refreshed_before_expiry() -> None:
    now = [0.0]
    fake = FakePiste()
    client = _client(fake, clock=lambda: now[0])
    assert client.access_token() == "tok-1"
    now[0] = 3000.0
    assert client.access_token() == "tok-1"
    now[0] = 3541.0  # past expires_in minus the 60 s margin
    assert client.access_token() == "tok-2"


def test_api_call_sends_bearer_token() -> None:
    fake = FakePiste()
    _client(fake).find_code()
    api_request = fake.requests[-1]
    assert api_request.headers["Authorization"] == "Bearer tok-1"


def test_401_on_api_refreshes_token_once_and_retries() -> None:
    fake = FakePiste()
    client = _client(fake)
    client.access_token()
    fake.reject_next_api_call = True
    assert client.find_code().id == CODE_ID
    assert fake.tokens_issued == 2


def test_oauth_failure_raises_without_leaking_secret() -> None:
    def reject(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(401, json={"error": "invalid_client"})

    client = LegifranceClient(
        ENDPOINTS,
        CLIENT_ID,
        CLIENT_SECRET,
        http=httpx2.Client(transport=httpx2.MockTransport(reject)),
    )
    with pytest.raises(PisteAuthError) as exc_info:
        client.access_token()
    assert "invalid_client" in str(exc_info.value)
    assert CLIENT_SECRET not in str(exc_info.value)


def test_quota_error_has_hint() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url == ENDPOINTS.oauth_url:
            return httpx2.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx2.Response(429, text="Too Many Requests")

    client = LegifranceClient(
        ENDPOINTS,
        CLIENT_ID,
        CLIENT_SECRET,
        http=httpx2.Client(transport=httpx2.MockTransport(handler)),
    )
    with pytest.raises(LegifranceApiError, match="quota") as exc_info:
        client.find_code()
    assert exc_info.value.status_code == 429


# --- operations -----------------------------------------------------------------------------


def test_find_code_matches_ceseda_title_despite_typographic_apostrophe() -> None:
    fake = FakePiste()
    code = _client(fake).find_code()
    assert code.id == CODE_ID
    assert fake.bodies("list/code")[0]["codeName"] == CESEDA_TITLE


def test_find_code_not_found() -> None:
    with pytest.raises(NotFoundError):
        _client(FakePiste()).find_code("Code inexistant")


def test_search_body_filters_code_number_and_date() -> None:
    fake = FakePiste()
    _client(fake).find_article_id("L. 422-1", date(2026, 11, 26))
    body = fake.bodies("search")[0]
    assert body["fond"] == "CODE_DATE"
    champ = body["recherche"]["champs"][0]
    assert champ["typeChamp"] == "NUM_ARTICLE"
    assert champ["criteres"][0] == {
        "typeRecherche": "EXACTE",
        "valeur": "L422-1",
        "operateur": "ET",
    }
    assert {"facette": "NOM_CODE", "valeurs": [CESEDA_TITLE]} in body["recherche"]["filtres"]
    assert {"facette": "DATE_VERSION", "singleDate": "2026-11-26"} in body["recherche"]["filtres"]


def test_article_in_force_returns_current_version() -> None:
    article = _client(FakePiste()).article_in_force("L422-1", date(2026, 11, 26))
    assert article.id == CURRENT_ID
    assert article.in_force_from == date(2021, 5, 1)
    assert article.in_force_to is None
    assert article.text == "Texte de test (en vigueur)."
    assert article.url(date(2026, 11, 26)).endswith(f"/codes/article_lc/{CURRENT_ID}/2026-11-26")


def test_article_in_force_follows_versions_when_search_hit_is_another_version() -> None:
    fake = FakePiste(search_hit_id=CURRENT_ID)
    article = _client(fake).article_in_force("L422-1", date(2020, 6, 1))
    assert article.id == OLD_ID
    assert [b["id"] for b in fake.bodies("consult/getArticle")] == [CURRENT_ID, OLD_ID]


def test_article_not_in_force_before_first_version() -> None:
    with pytest.raises(NotFoundError, match="in force"):
        _client(FakePiste()).article_in_force("L422-1", date(2010, 1, 1))


def test_article_number_not_in_search_results() -> None:
    with pytest.raises(NotFoundError):
        _client(FakePiste()).find_article_id("R999-9", date(2026, 11, 26))


# --- helpers --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("L422-1", "L422-1"), ("L. 422-1", "L422-1"), ("l 422-1", "L422-1"), (" R431-2 ", "R431-2")],
)
def test_normalise_article_num(raw: str, expected: str) -> None:
    assert normalise_article_num(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (MS_2021_05_01, date(2021, 5, 1)),
        (str(MS_2021_05_01), date(2021, 5, 1)),
        ("2021-05-01T00:00:00.000+0000", date(2021, 5, 1)),
        (MS_2999_01_01, None),
        (None, None),
    ],
)
def test_parse_date(raw: Any, expected: date | None) -> None:
    assert _parse_date(raw) == expected


# --- settings + CLI -------------------------------------------------------------------------


def _clear_piste_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("ID", "SECRET"):
        monkeypatch.delenv(f"PISTE_CLIENT_{name}", raising=False)
    for name in ("ENV", "OAUTH_URL", "API_URL"):
        monkeypatch.delenv(f"PISTE_{name}", raising=False)


def test_settings_default_to_sandbox_and_accept_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_piste_env(monkeypatch)
    monkeypatch.setenv("PISTE_CLIENT_ID", "x")
    monkeypatch.setenv("PISTE_CLIENT_SECRET", "y")
    assert PisteSettings(_env_file=None).endpoints() == SANDBOX
    monkeypatch.setenv("PISTE_ENV", "prod")
    assert PisteSettings(_env_file=None).endpoints() == PROD
    monkeypatch.setenv("PISTE_API_URL", "https://other.test/base/")
    assert PisteSettings(_env_file=None).endpoints().api_url == "https://other.test/base"


def test_cli_reports_missing_credentials(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _clear_piste_env(monkeypatch)
    assert check.main([], env_file=None) == check.EXIT_CONFIG_ERROR
    err = capsys.readouterr().err
    assert "PISTE_CLIENT_ID" in err and "PISTE_CLIENT_SECRET" in err


def test_cli_end_to_end_with_dump(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _clear_piste_env(monkeypatch)
    monkeypatch.setenv("PISTE_CLIENT_ID", CLIENT_ID)
    monkeypatch.setenv("PISTE_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setenv("PISTE_OAUTH_URL", ENDPOINTS.oauth_url)
    monkeypatch.setenv("PISTE_API_URL", ENDPOINTS.api_url)
    http = httpx2.Client(transport=httpx2.MockTransport(FakePiste()))
    argv = ["--article", "L422-1", "--date", "2026-11-26", "--dump", str(tmp_path)]

    assert check.main(argv, env_file=None, http=http) == check.EXIT_OK

    out = capsys.readouterr().out
    assert CODE_ID in out and CURRENT_ID in out and "2021-05-01 -> no end date" in out
    assert CLIENT_SECRET not in out and "tok-1" not in out
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "consult_getArticle.json",
        "list_code.json",
        "search.json",
    ]
