"""Minimal Légifrance API client over PISTE (OAuth2 client_credentials).

Endpoint paths and request bodies follow the Légifrance Swagger as reproduced by the pylegifrance
client (v1.7.2, models generated from `legifrance.json`, 2025-05-28). Confirmed against the PISTE
sandbox by a live run of `python -m ingestion.legifrance.check` on 2026-11-27 (OAuth with scope
`openid`, all three request bodies, and the response layouts recorded in
`tests/fixtures/legifrance/`).

Used endpoints (all POST, JSON):
- `list/code`          locate a code by title → LEGITEXT id
- `search`             fond CODE_DATE: article number in a code, version on a date → LEGIARTI id
- `consult/getArticle` one article version by LEGIARTI id (with all its versions)
"""

import html
import re
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import httpx2

from ingestion.legifrance.settings import PisteEndpoints

CESEDA_TITLE = "Code de l'entrée et du séjour des étrangers et du droit d'asile"
LEGIFRANCE_ARTICLE_URL = "https://www.legifrance.gouv.fr/codes/article_lc/{id}/{date}"
"""Public page of an article version on a date. URL pattern UNVERIFIED (seen on legifrance.gouv.fr
for /loda/article_lc/ and /codes/id/ pages)."""

# Légifrance encodes "no end date" as 2999-01-01.
_OPEN_END_YEAR = 2999
_TOKEN_EXPIRY_MARGIN_S = 60.0
_BODY_EXCERPT = 300
_MAX_RETRY_DELAY_S = 60.0
_TYPOGRAPHIC_APOSTROPHE = "\N{RIGHT SINGLE QUOTATION MARK}"

ResponseHook = Callable[[str, Any], None]
"""Called with (endpoint path, decoded JSON) after each successful API call."""


class LegifranceError(Exception):
    """Base error. Messages never contain credentials or tokens."""


class PisteAuthError(LegifranceError):
    pass


class LegifranceApiError(LegifranceError):
    def __init__(self, path: str, status_code: int, body: str) -> None:
        hint = " (quota exceeded, see PISTE > Applications > quotas)" if status_code == 429 else ""
        super().__init__(f"POST {path} -> HTTP {status_code}{hint}: {body[:_BODY_EXCERPT]}")
        self.path = path
        self.status_code = status_code


class NotFoundError(LegifranceError):
    pass


@dataclass(frozen=True)
class CodeRef:
    id: str
    title: str
    status: str | None


@dataclass(frozen=True)
class ArticleVersion:
    id: str
    num: str
    status: str | None
    in_force_from: date | None
    in_force_to: date | None
    """Exclusive end date; None when the version has no planned end."""
    text: str
    cid: str | None = None
    """Chronical id, stable across versions of the article."""
    section_path: tuple[str, ...] = ()
    """Titles of the enclosing sections, outermost first."""
    nota: str = ""
    text_html: str = ""
    """`texteHtml` as returned; needed for tables (e.g. CESEDA annexes)."""

    def in_force_on(self, on: date) -> bool:
        starts_ok = self.in_force_from is None or self.in_force_from <= on
        ends_ok = self.in_force_to is None or on < self.in_force_to
        return starts_ok and ends_ok

    def url(self, on: date) -> str:
        return LEGIFRANCE_ARTICLE_URL.format(id=self.id, date=on.isoformat())


class LegifranceClient:
    def __init__(
        self,
        endpoints: PisteEndpoints,
        client_id: str,
        client_secret: str,
        *,
        http: httpx2.Client | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_quota_retries: int = 4,
        on_response: ResponseHook | None = None,
    ) -> None:
        self._endpoints = endpoints
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http or httpx2.Client(timeout=httpx2.Timeout(30.0, connect=5.0))
        self._clock = clock
        self._sleep = sleep
        self._max_quota_retries = max_quota_retries
        self._on_response = on_response
        self._token: str | None = None
        self._token_expires_at = 0.0
        self.token_expires_in: int | None = None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "LegifranceClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # --- auth -----------------------------------------------------------------------------

    def access_token(self) -> str:
        """Return a cached token, fetching a new one when missing or about to expire."""
        if self._token is None or self._clock() >= self._token_expires_at:
            self._fetch_token()
        assert self._token is not None
        return self._token

    def _fetch_token(self) -> None:
        try:
            response = self._http.post(
                self._endpoints.oauth_url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "scope": "openid",
                },
            )
        except httpx2.HTTPError as exc:
            raise PisteAuthError(f"Cannot reach PISTE OAuth: {type(exc).__name__}") from None
        if response.status_code != 200:
            raise PisteAuthError(
                f"PISTE OAuth -> HTTP {response.status_code}: {response.text[:_BODY_EXCERPT]}"
            )
        payload = response.json()
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise PisteAuthError("PISTE OAuth response has no access_token")
        expires_in = int(payload.get("expires_in", 0))
        self._token = token
        self.token_expires_in = expires_in
        self._token_expires_at = self._clock() + max(expires_in - _TOKEN_EXPIRY_MARGIN_S, 0.0)

    # --- transport ------------------------------------------------------------------------

    def post(self, path: str, body: dict[str, Any]) -> Any:
        """POST JSON to an API path. On 401, refresh the token once and retry. On 429 (quota),
        wait (`Retry-After` seconds, else exponential backoff) and retry up to
        `max_quota_retries` times."""
        url = f"{self._endpoints.api_url}/{path}"
        response = self._send(url, body)
        if response.status_code == 401:
            self._token = None
            response = self._send(url, body)
        for attempt in range(self._max_quota_retries):
            if response.status_code != 429:
                break
            self._sleep(_retry_delay(response, attempt))
            response = self._send(url, body)
        if response.status_code != 200:
            raise LegifranceApiError(path, response.status_code, response.text)
        payload = response.json()
        if self._on_response is not None:
            self._on_response(path, payload)
        return payload

    def _send(self, url: str, body: dict[str, Any]) -> httpx2.Response:
        try:
            return self._http.post(
                url,
                json=body,
                headers={
                    "Authorization": f"Bearer {self.access_token()}",
                    "Accept": "application/json",
                },
            )
        except httpx2.HTTPError as exc:
            raise LegifranceError(f"Cannot reach Légifrance API: {type(exc).__name__}") from None

    # --- operations -----------------------------------------------------------------------

    def table_of_contents(self, text_id: str, on: date, nature: str = "CODE") -> dict[str, Any]:
        """`consult/legi/tableMatieres` (the `consult/code/tableMatieres` variant is deprecated
        in the Swagger)."""
        payload: dict[str, Any] = self.post(
            "consult/legi/tableMatieres",
            {"textId": text_id, "date": on.isoformat(), "nature": nature},
        )
        return payload

    def jorf_text(self, text_cid: str) -> dict[str, Any]:
        """`consult/jorf`: a text as published in the Journal officiel."""
        payload: dict[str, Any] = self.post("consult/jorf", {"textCid": text_cid})
        return payload

    def find_code(self, title: str = CESEDA_TITLE) -> CodeRef:
        payload = self.post(
            "list/code",
            {"codeName": title, "pageNumber": 1, "pageSize": 10, "states": ["VIGUEUR"]},
        )
        wanted = _normalise_title(title)
        for item in payload.get("results") or []:
            if _normalise_title(str(item.get("titre", ""))) == wanted and item.get("id"):
                return CodeRef(id=item["id"], title=item["titre"], status=item.get("etat"))
        raise NotFoundError(f"No code titled {title!r} in list/code results")

    def find_article_id(self, num: str, on: date, code_title: str = CESEDA_TITLE) -> str:
        """LEGIARTI id of article `num` of the code, as returned by a CODE_DATE search on `on`."""
        num = normalise_article_num(num)
        payload = self.post("search", _code_date_search_body(num, on, code_title))
        for candidate in _search_article_hits(payload):
            if normalise_article_num(str(candidate.get("num", ""))) == num:
                article_id = candidate.get("id")
                if isinstance(article_id, str) and article_id.startswith("LEGIARTI"):
                    return article_id
        raise NotFoundError(f"Article {num} not found in {code_title!r} on {on.isoformat()}")

    def get_article(self, article_id: str) -> tuple[ArticleVersion, list[dict[str, Any]]]:
        """One article version, plus the raw `articleVersions` list (all versions)."""
        payload = self.post("consult/getArticle", {"id": article_id})
        article = payload.get("article")
        if not isinstance(article, dict):
            raise NotFoundError(f"consult/getArticle returned no article for {article_id}")
        return _parse_article(article), list(article.get("articleVersions") or [])

    def article_in_force(
        self, num: str, on: date, code_title: str = CESEDA_TITLE
    ) -> ArticleVersion:
        """The version of article `num` in force on `on`, checked against its own dates."""
        return self.version_in_force(self.find_article_id(num, on, code_title), on, label=num)

    def version_in_force(self, article_id: str, on: date, label: str = "") -> ArticleVersion:
        """The version in force on `on` of the article that `article_id` (any version) belongs
        to. Raises `NotFoundError` if none is."""
        article, versions = self.get_article(article_id)
        num = label or article.num
        if article.in_force_on(on):
            return article
        # The search hit was another version: pick the one whose period covers `on`.
        for version in versions:
            start, end = _parse_date(version.get("dateDebut")), _parse_date(version.get("dateFin"))
            covers = (start is None or start <= on) and (end is None or on < end)
            if covers and version.get("id") and version["id"] != article.id:
                candidate, _ = self.get_article(version["id"])
                if candidate.in_force_on(on):
                    return candidate
        raise NotFoundError(f"No version of article {num} is in force on {on.isoformat()}")


# --- pure helpers (unit-tested) -------------------------------------------------------------


def normalise_article_num(num: str) -> str:
    """'L. 422-1' / 'l 422-1' / 'L422-1' -> 'L422-1'."""
    match = re.fullmatch(r"\s*([A-Za-z]+)\.?\s*(\S.*?)\s*", num)
    if not match:
        return num.strip()
    return f"{match.group(1).upper()}{match.group(2).replace(' ', '')}"


def _normalise_title(title: str) -> str:
    text = unicodedata.normalize("NFC", title).replace(_TYPOGRAPHIC_APOSTROPHE, "'")
    return " ".join(text.casefold().split())


def _code_date_search_body(num: str, on: date, code_title: str) -> dict[str, Any]:
    return {
        "fond": "CODE_DATE",
        "recherche": {
            "champs": [
                {
                    "typeChamp": "NUM_ARTICLE",
                    "operateur": "ET",
                    "criteres": [{"typeRecherche": "EXACTE", "valeur": num, "operateur": "ET"}],
                }
            ],
            "filtres": [
                {"facette": "NOM_CODE", "valeurs": [code_title]},
                {"facette": "DATE_VERSION", "singleDate": on.isoformat()},
            ],
            "operateur": "ET",
            "sort": "PERTINENCE",
            "typePagination": "DEFAUT",
            "pageNumber": 1,
            "pageSize": 10,
        },
    }


def _search_article_hits(payload: Any) -> list[dict[str, Any]]:
    """Flatten results -> sections -> extracts (type 'articles'), the search response layout."""
    hits: list[dict[str, Any]] = []
    for result in payload.get("results") or []:
        for section in result.get("sections") or []:
            for extract in section.get("extracts") or []:
                if extract.get("type") == "articles":
                    hits.append(extract)
    return hits


def _retry_delay(response: httpx2.Response, attempt: int) -> float:
    """Seconds to wait after a 429: the `Retry-After` header (seconds) when present and valid,
    else 1, 2, 4, 8... seconds; never more than `_MAX_RETRY_DELAY_S`."""
    header = response.headers.get("Retry-After", "")
    delay = float(header) if header.isdigit() else float(2**attempt)
    return min(delay, _MAX_RETRY_DELAY_S)


def _parse_date(value: Any) -> date | None:
    """Légifrance dates come as epoch milliseconds (int or digit string) or ISO strings."""
    if value is None or value == "":
        return None
    if isinstance(value, int | float) or (isinstance(value, str) and value.isdigit()):
        parsed = datetime.fromtimestamp(int(value) / 1000, tz=UTC).date()
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("+0000", "+00:00")).date()
    else:
        raise ValueError(f"Unexpected date value: {value!r}")
    return None if parsed.year >= _OPEN_END_YEAR else parsed


def _html_to_text(html: str) -> str:
    text = re.sub(r"<br\s*/?>|</p>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _parse_article(article: dict[str, Any]) -> ArticleVersion:
    text = article.get("texte") or _html_to_text(article.get("texteHtml") or "")
    nota = article.get("nota") or _html_to_text(article.get("notaHtml") or "")
    sections = html.unescape(str(article.get("fullSectionsTitre") or ""))
    return ArticleVersion(
        id=str(article["id"]),
        num=str(article.get("num", "")),
        status=article.get("etat"),
        in_force_from=_parse_date(article.get("dateDebut")),
        in_force_to=_parse_date(article.get("dateFin")),
        text=str(text).strip(),
        cid=article.get("cid"),
        section_path=tuple(t.strip() for t in sections.split(" > ") if t.strip()),
        nota=str(nota).strip(),
        text_html=str(article.get("texteHtml") or ""),
    )
