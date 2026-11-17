"""Access boundary tests for LangGraph verifying role isolation, unknown roles, and spoofing resistance."""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.auth.jwt import create_access_token
from app.config import settings
from app.main import app
from app.orchestration.graph import chat_graph
from app.orchestration.state import ChatState

client = TestClient(app)


@pytest.fixture(autouse=True)
def force_mock_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forces mock LLM provider and calibrated threshold across all access tests."""
    monkeypatch.setattr(settings, "llm_provider", "mock")
    monkeypatch.setattr(settings, "orchestrator", "langgraph")
    monkeypatch.setattr(settings, "rag_score_threshold", 0.80)
    monkeypatch.setattr("time.sleep", lambda _: None)


def _parse_sse(raw_text: str) -> tuple[str, list[str]]:
    """Helper extracting concatenated tokens and source list from SSE text."""
    tokens: list[str] = []
    sources: list[str] = []
    for line in raw_text.splitlines():
        if line.startswith("data: "):
            payload = json.loads(line[6:])
            if "token" in payload:
                tokens.append(payload["token"])
            if payload.get("done") is True:
                sources = payload.get("sources", [])
    return "".join(tokens), sources


@pytest.mark.parametrize(
    "question",
    [
        "What are the salary bands for junior and senior levels?",
        "How much annual bonus percentage is paid out for an outstanding rating?",
        "What are the appraisal calibration rules and hike percentages?",
        "Tell me the CTC breakdown and executive compensation details.",
        "What rating is needed for promotion and what is the salary increase?",
    ],
)
def test_employee_never_receives_hr_chunks(question: str) -> None:
    """Verifies that employees never receive hr_benefits chunks on paraphrased HR queries."""
    token = create_access_token(user_id="emp_probe", role="employee")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": question}

    resp = client.post("/chat", json=payload, headers=headers)
    assert resp.status_code == 200
    text, sources = _parse_sse(resp.text)

    # Confidential HR document must never leak to employee
    assert "hr_benefits.md" not in sources


@pytest.mark.parametrize(
    "question,expected_doc",
    [
        ("What are the salary bands for junior and senior levels?", "hr_benefits.md"),
        ("What is the leave policy and how many paid leaves are allowed?", "employee_handbook.md"),
    ],
)
def test_hr_receives_both_all_and_hr_docs(question: str, expected_doc: str) -> None:
    """Verifies that HR users can access both general company docs and HR confidential docs."""
    token = create_access_token(user_id="hr_probe", role="hr")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": question}

    resp = client.post("/chat", json=payload, headers=headers)
    assert resp.status_code == 200
    text, sources = _parse_sse(resp.text)
    assert expected_doc in sources
    assert len(text) > 0


def test_unknown_role_rejected_by_auth() -> None:
    """Verifies that an unknown role in JWT returns HTTP 403 before reaching graph."""
    token = create_access_token(user_id="contractor_user", role="contractor")
    headers = {"Authorization": f"Bearer {token}"}
    resp = client.post("/chat", json={"message": "What is the leave policy?"}, headers=headers)
    assert resp.status_code == 403
    assert "not permitted" in resp.json()["detail"].lower()


def test_unknown_role_in_graph_state_denies_and_returns_nothing() -> None:
    """Verifies that graph directly executing with unknown role returns status denied and 0 chunks."""
    state: ChatState = {
        "user_id": "test_direct",
        "role": "unauthorized_role",
        "question": "What is the leave policy?",
        "history": [],
        "chunks": [],
        "sources": [],
        "answer_text": "",
        "status": "initialized",
    }
    result = chat_graph.invoke(state)
    assert result["status"] == "completed"
    assert result["sources"] == []
    assert "enough information" in result["answer_text"]


def test_role_field_in_request_body_is_ignored() -> None:
    """Verifies that body role spoofing (e.g. role='hr') by employee is ignored."""
    token = create_access_token(user_id="emp_probe", role="employee")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "message": "What are the official salary bands for L1 to L3?",
        "role": "hr",
    }
    resp = client.post("/chat", json=payload, headers=headers)
    assert resp.status_code == 200
    text, sources = _parse_sse(resp.text)
    assert "hr_benefits.md" not in sources
    assert sources == []
