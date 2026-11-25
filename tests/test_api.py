"""Integration and unit tests for FastAPI health and chat SSE endpoints across both orchestrators."""
from __future__ import annotations

import json
from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.auth.jwt import create_access_token
from app.config import settings
from app.main import app
from app.middleware.rate_limit import limiter

client = TestClient(app)


@pytest.fixture(autouse=True)
def force_mock_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Forces mock LLM provider and calibrated threshold across all tests."""
    monkeypatch.setattr(settings, "llm_provider", "mock")
    monkeypatch.setattr(settings, "rag_score_threshold", 0.80)


@pytest.fixture(params=["langgraph"])
def orchestrator_mode(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Runs test in 'langgraph' orchestrator mode."""
    mode: str = request.param
    monkeypatch.setattr(settings, "orchestrator", mode)
    return mode


def _parse_sse(raw_text: str) -> tuple[str, list[str]]:
    """Extracts joined text and sources list from raw SSE data lines."""
    tokens: list[str] = []
    sources: list[str] = []
    for line in raw_text.splitlines():
        if line.startswith("data: "):
            data = json.loads(line[6:])
            if "token" in data:
                tokens.append(data["token"])
            if data.get("done") is True:
                sources = data.get("sources", [])
    return "".join(tokens), sources


def test_health_endpoint(orchestrator_mode: str) -> None:
    """Verifies that GET /health returns 200 with status ok, active orchestrator, and llm_provider."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "1.0.0"
    assert data["orchestrator"] == orchestrator_mode
    assert data["llm_provider"] in ("mock", "groq", "openai", "gemini")


def test_health_llm_provider_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that placeholder or missing keys resolve to 'mock' and never leak secrets."""
    # When configured for groq but with placeholder key
    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(settings, "groq_api_key", "your-groq-api-key-here")
    resp = client.get("/health")
    assert resp.json()["llm_provider"] == "mock"
    assert "your-groq" not in resp.text

    # When configured with a valid key
    monkeypatch.setattr(settings, "llm_provider", "groq")
    monkeypatch.setattr(settings, "groq_api_key", "gsk_test_valid_fake_key_12345")
    resp_valid = client.get("/health")
    assert resp_valid.json()["llm_provider"] == "groq"
    assert "gsk_test" not in resp_valid.text



def test_chat_unauthorized(orchestrator_mode: str) -> None:
    """Verifies that POST /chat rejects requests missing a Bearer JWT."""
    resp = client.post("/chat", json={"message": "Hello"})
    assert resp.status_code == 401
    assert "credentials" in resp.json()["detail"].lower()


def test_chat_invalid_token(orchestrator_mode: str) -> None:
    """Verifies that POST /chat rejects invalid JWT tokens."""
    headers = {"Authorization": "Bearer invalid.jwt.token"}
    resp = client.post("/chat", json={"message": "Hello"}, headers=headers)
    assert resp.status_code == 401


def test_chat_employee_allowed(orchestrator_mode: str) -> None:
    """Verifies that an employee receives answers from employee handbook."""
    token = create_access_token(user_id="emp_test", role="employee")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": "What is the leave policy?"}

    resp = client.post("/chat", json=payload, headers=headers)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    text, sources = _parse_sse(resp.text)
    assert "employee_handbook.md" in sources


def test_chat_employee_forbidden_doc(orchestrator_mode: str) -> None:
    """Verifies that an employee cannot access confidential HR salary bands."""
    token = create_access_token(user_id="emp_test", role="employee")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": "What are the salary bands for L1 and L2?"}

    resp = client.post("/chat", json=payload, headers=headers)
    assert resp.status_code == 200
    text, sources = _parse_sse(resp.text)
    assert "hr_benefits.md" not in sources
    assert "enough information" in text
    assert sources == []


def test_chat_hr_allowed_doc(orchestrator_mode: str) -> None:
    """Verifies that an HR user successfully retrieves HR confidential data."""
    token = create_access_token(user_id="hr_test", role="hr")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": "What are the salary bands for L1 and L2?"}

    resp = client.post("/chat", json=payload, headers=headers)
    assert resp.status_code == 200
    text, sources = _parse_sse(resp.text)
    assert "hr_benefits.md" in sources


def test_chat_role_spoofing_ignored(orchestrator_mode: str) -> None:
    """Verifies that role injected into request body is completely ignored."""
    token = create_access_token(user_id="emp_test", role="employee")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "message": "What are the salary bands for L1 and L2?",
        "role": "hr",
    }
    resp = client.post("/chat", json=payload, headers=headers)
    assert resp.status_code == 200
    text, sources = _parse_sse(resp.text)
    assert "hr_benefits.md" not in sources
    assert sources == []


def test_chat_message_length_validation(orchestrator_mode: str) -> None:
    """Verifies that messages exceeding max length are rejected."""
    token = create_access_token(user_id="emp_test", role="employee")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": "a" * 1500}

    resp = client.post("/chat", json=payload, headers=headers)
    assert resp.status_code == 422


def test_rate_limiter(orchestrator_mode: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that exceeding the rate limit returns HTTP 429."""
    monkeypatch.setattr(settings, "rate_limit", "5/minute")
    limiter.reset()
    token = create_access_token(user_id="rate_limited_user", role="employee")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": "What is the leave policy?"}

    hit_429 = False
    for _ in range(10):
        resp = client.post("/chat", json=payload, headers=headers)
        if resp.status_code == 429:
            hit_429 = True
            break
    limiter.reset()
    assert hit_429 is True


def test_chat_temporary_mode_does_not_persist_session(
    orchestrator_mode: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verifies temporary chat streams answers without creating or storing server sessions while keeping RBAC."""
    from app.rag.session_manager import _MEMORY_SESSIONS, reset_memory_store, session_manager

    reset_memory_store()
    called = False

    def fail_if_called(*args: object, **kwargs: object) -> object:
        nonlocal called
        called = True
        raise AssertionError("Session persistence must not run in temporary mode")

    monkeypatch.setattr(session_manager, "get_or_create_session", fail_if_called)
    monkeypatch.setattr(session_manager, "save_user_message", fail_if_called)
    monkeypatch.setattr(session_manager, "save_assistant_message", fail_if_called)

    token = create_access_token(user_id="temp_emp", role="employee")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "message": "What is the leave policy?",
        "history": [{"role": "user", "content": "Hello"}],
        "temporary": True,
    }
    resp = client.post("/chat", json=payload, headers=headers)
    assert resp.status_code == 200
    assert called is False
    assert "x-session-id" not in resp.headers
    assert resp.headers.get("x-temporary-chat") == "true"
    assert len(_MEMORY_SESSIONS) == 0

    events = [
        json.loads(line[6:])
        for line in resp.text.splitlines()
        if line.startswith("data: ")
    ]
    done_ev = next(ev for ev in events if ev.get("done") is True)
    assert done_ev.get("temporary") is True
    assert "session_id" not in done_ev
    assert "employee_handbook.md" in done_ev["sources"]

    # RBAC remains enforced in temporary mode
    hr_resp = client.post(
        "/chat",
        json={"message": "What are the salary bands for L1 and L2?", "temporary": True},
        headers=headers,
    )
    assert hr_resp.status_code == 200
    _, hr_sources = _parse_sse(hr_resp.text)
    assert "hr_benefits.md" not in hr_sources

