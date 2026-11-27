import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.ask import get_agent, get_guard, get_query_logger
from app.limits import Admission
from app.main import create_app
from rag.graph import Deps, build_graph
from tests.test_graph import SHEET, SHEET_2, SUPPORTED, FakeAnswerer, FakeRetriever

QUESTION = "Quelle est la durée de la carte étudiant fictive pour Zorglub ?"


class AllowAll:
    def admit(self, client_ip: str) -> Admission:
        return Admission("ok")


@dataclass
class Refuse:
    outcome: Any
    seen: list[str] = field(default_factory=list)

    def admit(self, client_ip: str) -> Admission:
        self.seen.append(client_ip)
        return Admission(self.outcome, 3600)


@pytest.fixture
def logs() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def client(logs: list[dict[str, Any]]) -> TestClient:
    app = create_app()
    graph = build_graph(Deps(FakeRetriever([SHEET, SHEET_2]), FakeAnswerer(SUPPORTED)))
    app.dependency_overrides[get_agent] = lambda: graph.invoke
    app.dependency_overrides[get_query_logger] = lambda: logs.append
    app.dependency_overrides[get_guard] = lambda: AllowAll()
    return TestClient(app)


def test_ask_returns_a_cited_answer_with_notices(client: TestClient) -> None:
    response = client.post("/ask", json={"question": QUESTION, "stage": "renewing"})
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "answer"
    assert body["sentences"][0] == {"text": "La carte est valable 7 lunes.", "sources": [1]}
    assert body["sources"][0]["url"] == "https://example.test/F1"
    assert body["notices"][0].startswith("Information générale")


def test_logs_hold_metadata_only(client: TestClient, logs: list[dict[str, Any]]) -> None:
    client.post("/ask", json={"question": QUESTION, "stage": "renewing", "lang": "en"})
    assert len(logs) == 1
    entry = logs[0]
    assert entry["journey_stage"] == "renewing"
    assert entry["retrieved_chunk_ids"] == ["fact_sheet:F1:0", "fact_sheet:F2:0"]
    assert entry["eval_flags"]["kind"] == "answer"
    serialised = json.dumps(entry, default=str)
    assert "Zorglub" not in serialised and "durée" not in serialised


def test_requested_language_wins_over_detection(client: TestClient) -> None:
    body = client.post("/ask", json={"question": QUESTION, "lang": "en"}).json()
    assert body["lang"] == "en"
    assert body["notices"][0].startswith("General information")


@pytest.mark.parametrize(
    "payload",
    [
        {"question": "?"},
        {"question": "x" * 1001},
        {"question": QUESTION, "lang": "de"},
        {"question": QUESTION, "stage": "retired"},
        {"question": QUESTION, "nationality": "martian"},
        {},
    ],
)
def test_invalid_requests_are_rejected(client: TestClient, payload: dict[str, Any]) -> None:
    assert client.post("/ask", json=payload).status_code == 422


def test_a_failing_log_write_does_not_fail_the_answer(client: TestClient) -> None:
    def broken(_: dict[str, Any]) -> None:
        raise RuntimeError("db down")

    client.app.dependency_overrides[get_query_logger] = lambda: broken  # type: ignore[attr-defined]
    assert client.post("/ask", json={"question": QUESTION}).status_code == 200


@pytest.mark.parametrize(("outcome", "status"), [("rate_limited", 429), ("spend_cap", 503)])
def test_guardrails_refuse_before_any_model_call(
    client: TestClient, logs: list[dict[str, Any]], outcome: str, status: int
) -> None:
    guard = Refuse(outcome)
    client.app.dependency_overrides[get_guard] = lambda: guard  # type: ignore[attr-defined]
    response = client.post("/ask", json={"question": QUESTION})
    assert response.status_code == status
    assert response.json() == {"detail": outcome}
    assert response.headers["retry-after"] == "3600"
    assert logs == []  # the agent did not run
    assert guard.seen == ["testclient"]
