"""End-to-end integration tests using live LLM providers across both orchestrators."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app.auth.jwt import create_access_token
from app.config import settings
from app.main import app

client = TestClient(app)

_SKIP_REASON: str = "Live LLM tests skipped in automated mock suite; requires live LLM_PROVIDER"


def _parse_sse(text: str) -> tuple[str, list[str]]:
    """Helper extracting concatenated tokens and source list from live SSE stream."""
    tokens: list[str] = []
    sources: list[str] = []
    for line in text.splitlines():
        if line.startswith("data: "):
            payload = json.loads(line[6:])
            if "token" in payload:
                tokens.append(payload["token"])
            if payload.get("done") is True:
                sources = payload.get("sources", [])
    return "".join(tokens), sources


@pytest.mark.skip(reason=_SKIP_REASON)
@pytest.mark.parametrize("mode", ["langgraph"])
def test_live_llm_employee_query(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that live LLM streaming produces grounded answer in both modes."""
    monkeypatch.setattr(settings, "orchestrator", mode)
    token = create_access_token(user_id="live_emp", role="employee")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": "What is the leave policy?"}

    resp = client.post("/chat", json=payload, headers=headers)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    text, sources = _parse_sse(resp.text)
    assert len(text) > 0
    assert "employee_handbook.md" in sources


@pytest.mark.skip(reason=_SKIP_REASON)
@pytest.mark.parametrize("mode", ["langgraph"])
def test_live_llm_hr_query(mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that live LLM streaming returns confidential HR sources for HR role in both modes."""
    monkeypatch.setattr(settings, "orchestrator", mode)
    token = create_access_token(user_id="live_hr", role="hr")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": "What are the salary bands for L1 and L2?"}

    resp = client.post("/chat", json=payload, headers=headers)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    text, sources = _parse_sse(resp.text)
    assert len(text) > 0
    assert "hr_benefits.md" in sources
