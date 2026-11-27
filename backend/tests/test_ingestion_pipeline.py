"""Manifest validation and pipeline loaders over mocked HTTP. Recorded responses and sheets are
Légifrance / Service-Public.gouv.fr (DILA, Licence Ouverte 2.0); synthetic article texts are
placeholders, not legal content."""

import copy
import io
import json
import typing
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import httpx2
import pytest

from db.models import JOURNEY_STAGES
from ingestion import __main__ as cli
from ingestion.legifrance.client import LegifranceClient
from ingestion.legifrance.settings import PisteEndpoints
from ingestion.manifest import JourneyStage, Manifest, load_manifest
from ingestion.pipeline import load_fact_sheets, load_laws, load_treaties, read_sheets

FIXTURES = Path(__file__).parent / "fixtures"
ENDPOINTS = PisteEndpoints(oauth_url="https://oauth.test/token", api_url="https://api.test/lf")
ON = date(2026, 11, 27)
L422_IDS = {
    "LEGIARTI000042776689": ("LEGIARTI000042771654", "L422-1"),
    "LEGIARTI000042776687": ("LEGIARTI000042771656", "L422-2"),
    "LEGIARTI000042776685": ("LEGIARTI000042771658", "L422-3"),
}
THIRD_AMENDMENT = "JORFTEXT000000599731"


def _json(path: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / path).read_text(encoding="utf-8"))
    return data


def _manifest(**overrides: Any) -> Manifest:
    data: dict[str, Any] = {
        "version": 1,
        "fact_sheets": {
            "feed_url": "https://feed.test/vosdroits.zip",
            "sheets": [
                {"id": "F2231", "title": "T", "journey_stages": ["arriving", "renewing"]},
                {
                    "id": "F2733",
                    "title": "T",
                    "journey_stages": ["working"],
                    "nationality_scope": "algerian",
                },
            ],
        },
        "laws": {
            "codes": [
                {
                    "text_id": "LEGITEXT000006070158",
                    "label": "CESEDA",
                    "title": "Code de l'entrée et du séjour des étrangers et du droit d'asile",
                }
            ]
        },
        "treaties": {
            "texts": [
                {"jorf_cid": THIRD_AMENDMENT, "title": "3e avenant", "status": "included"},
                {"jorf_cid": "JORFTEXT000000694290", "title": "Accord", "status": "pending"},
            ]
        },
    }
    data.update(overrides)
    return Manifest.model_validate(data)


def _feed(*sheet_ids: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for sheet_id in sheet_ids:
            path = FIXTURES / "service_public" / f"{sheet_id}.xml"
            archive.writestr(f"{sheet_id}.xml", path.read_bytes())
    return buffer.getvalue()


def _article(article_id: str) -> dict[str, Any]:
    recorded = _json("legifrance/consult_getArticle.json")
    if article_id == "LEGIARTI000042776689":
        return recorded
    cid, num = L422_IDS[article_id]
    payload = copy.deepcopy(recorded)
    payload["article"].update(
        id=article_id, cid=cid, num=num, texte=f"Texte de test ({num}).", nota=None
    )
    payload["article"]["articleVersions"] = []
    return payload


class FakeLegifrance:
    def __init__(self) -> None:
        self.article_requests: list[str] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        if request.url == ENDPOINTS.oauth_url:
            return httpx2.Response(200, json={"access_token": "t", "expires_in": 3600})
        path = request.url.path.removeprefix("/lf/")
        body = json.loads(request.content)
        if path == "consult/legi/tableMatieres":
            return httpx2.Response(200, json=_json("legifrance/consult_legi_tableMatieres.json"))
        if path == "consult/getArticle":
            self.article_requests.append(body["id"])
            if body["id"] in L422_IDS:
                return httpx2.Response(200, json=_article(body["id"]))
        if path == "consult/jorf" and body["textCid"] == THIRD_AMENDMENT:
            return httpx2.Response(
                200, json=_json(f"legifrance/consult_jorf_{THIRD_AMENDMENT}.json")
            )
        return httpx2.Response(404, text="no fixture")


def _client(fake: FakeLegifrance) -> LegifranceClient:
    http = httpx2.Client(transport=httpx2.MockTransport(fake))
    return LegifranceClient(ENDPOINTS, "id", "secret", http=http, sleep=lambda _: None)


def test_repository_manifest_is_valid() -> None:
    manifest = load_manifest()
    ids = [s.id for s in manifest.fact_sheets.sheets]
    assert len(ids) == len(set(ids))
    assert not set(ids) & {s.id for s in manifest.fact_sheets.excluded}
    assert manifest.laws.codes[0].text_id == "LEGITEXT000006070158"


def test_journey_stage_literal_matches_database_values() -> None:
    assert set(typing.get_args(JourneyStage)) == set(JOURNEY_STAGES)


def test_read_sheets_reports_missing_sheet() -> None:
    sheets, errors = read_sheets(_manifest(), _feed("F2231"))
    assert [s.id for s in sheets] == ["F2231"]
    assert errors == ["F2733: not in the feed"]


def test_fact_sheet_drafts_carry_manifest_metadata() -> None:
    manifest = _manifest()
    sheets, errors = read_sheets(manifest, _feed("F2231", "F2733"))
    result = load_fact_sheets(manifest, sheets, errors)
    assert result.complete
    assert result.sources == {("fact_sheet", "F2231"), ("fact_sheet", "F2733")}
    algerian = {d.source_id for d in result.drafts if d.meta.nationality_scope == "algerian"}
    assert algerian == {"F2733"}


def test_laws_are_cited_articles_with_inherited_stages() -> None:
    manifest = _manifest()
    sheets, _ = read_sheets(manifest, _feed("F2231"))
    fake = FakeLegifrance()
    result = load_laws(manifest, sheets, _client(fake), ON)
    by_source = {d.source_id: d for d in result.drafts}
    for cid, _num in L422_IDS.values():
        assert by_source[cid].meta.journey_stages == ("arriving", "renewing")
        assert by_source[cid].meta.nationality_scope == "all"
    assert by_source["LEGIARTI000042771654"].title == "CESEDA, article L422-1"
    # Sections outside the trimmed TOC are reported, not fetched.
    assert any("not in force" in n for n in result.notes)
    # Article references not in the fake (404) are errors: the law set is incomplete.
    assert not result.complete


def test_excluded_articles_are_never_fetched() -> None:
    code = (
        _manifest().laws.codes[0].model_copy(update={"exclude_articles": ("LEGIARTI000042776687",)})
    )
    manifest = _manifest(laws={"codes": [code.model_dump()]})
    sheets, _ = read_sheets(manifest, _feed("F2231"))
    fake = FakeLegifrance()
    load_laws(manifest, sheets, _client(fake), ON)
    assert "LEGIARTI000042776687" not in fake.article_requests
    assert "LEGIARTI000042776689" in fake.article_requests


def test_treaties_load_included_texts_and_note_pending_ones() -> None:
    result = load_treaties(_manifest(), _client(FakeLegifrance()))
    assert result.complete
    assert result.sources == {("treaty", THIRD_AMENDMENT)}
    assert {d.meta.nationality_scope for d in result.drafts} == {"algerian"}
    assert result.notes == ["JORFTEXT000000694290: pending (Accord)"]


def test_cli_rejects_missing_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = cli.main(["run", "--all", "--dry-run", "--manifest", str(tmp_path / "none.yaml")])
    assert code == cli.EXIT_CONFIG_ERROR
    assert "manifest" in capsys.readouterr().err
