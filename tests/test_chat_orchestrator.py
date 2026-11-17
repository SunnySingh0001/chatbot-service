"""Integration tests for POST /chat under ORCHESTRATOR=classic vs ORCHESTRATOR=langgraph."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.auth.jwt import create_access_token
from app.config import settings
from app.main import app
from app.orchestration.state import ChatState
from app.api.chat import _stream_langgraph_sse

client = TestClient(app)


@pytest.fixture(autouse=True)
def mock_llm_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensures all tests run against mock LLM provider without external API calls."""
    monkeypatch.setattr(settings, "llm_provider", "mock")


def _parse_sse_response(text: str) -> tuple[str, list[str]]:
    """Helper parsing raw SSE text into accumulated answer and final source list."""
    tokens: list[str] = []
    sources: list[str] = []
    for line in text.strip().split("\n\n"):
        if line.startswith("data: "):
            payload = json.loads(line[6:])
            if "token" in payload:
                tokens.append(payload["token"])
            if "done" in payload and payload["done"]:
                sources = payload.get("sources", [])
    return "".join(tokens), sources


def test_chat_classic_mode_streams_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that classic mode streams tokens and sources via SSE."""
    monkeypatch.setattr(settings, "orchestrator", "classic")
    token = create_access_token(user_id="emp_user", role="employee")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": "What is the probation period?"}

    resp = client.post("/chat", json=payload, headers=headers)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    text, sources = _parse_sse_response(resp.text)
    assert len(text) > 0
    assert "employee_handbook.md" in sources


def test_chat_langgraph_mode_streams_sse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that langgraph mode streams tokens incrementally and citations via SSE."""
    monkeypatch.setattr(settings, "orchestrator", "langgraph")
    token = create_access_token(user_id="emp_user", role="employee")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": "What is the probation period?"}

    resp = client.post("/chat", json=payload, headers=headers)
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]
    text, sources = _parse_sse_response(resp.text)
    assert len(text) > 0
    assert "employee_handbook.md" in sources


def test_chat_langgraph_unauthorized_rejected_before_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that missing or invalid auth is rejected by FastAPI before reaching graph."""
    monkeypatch.setattr(settings, "orchestrator", "langgraph")
    payload = {"message": "What is the probation period?"}

    # Missing token -> 401
    resp_no_token = client.post("/chat", json=payload)
    assert resp_no_token.status_code == 401

    # Malformed token -> 401
    resp_bad_token = client.post("/chat", json=payload, headers={"Authorization": "Bearer badtoken"})
    assert resp_bad_token.status_code == 401


def test_chat_langgraph_message_length_rejected_before_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that oversize messages are rejected with 422 before reaching graph."""
    monkeypatch.setattr(settings, "orchestrator", "langgraph")
    token = create_access_token(user_id="emp_user", role="employee")
    headers = {"Authorization": f"Bearer {token}"}
    payload = {"message": "x" * (settings.max_message_length + 1)}

    resp = client.post("/chat", json=payload, headers=headers)
    assert resp.status_code == 422


@pytest.mark.anyio
async def test_chat_client_disconnect_stops_stream() -> None:
    """Verifies that client disconnect stops token streaming immediately."""
    call_count = 0

    async def mock_is_disconnected() -> bool:
        nonlocal call_count
        call_count += 1
        return call_count > 1

    mock_request = AsyncMock()
    mock_request.is_disconnected = mock_is_disconnected

    state: ChatState = {
        "user_id": "emp_user",
        "role": "employee",
        "question": "What is the probation period?",
        "history": [],
        "chunks": [],
        "sources": [],
        "answer_text": "",
        "status": "initialized",
    }

    yielded_events: list[str] = []
    async for event in _stream_langgraph_sse(mock_request, state):
        yielded_events.append(event)

    # Disconnect stops further token processing and avoids done event
    assert len(yielded_events) <= 2
    assert not any('"done": true' in ev for ev in yielded_events)


@pytest.mark.anyio
async def test_chat_langgraph_error_returns_generic_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that exceptions inside the graph yield safe generic error message without leaking internals."""
    from app.api import chat

    async def broken_astream(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("Internal database connection failed with secret credentials exposed")
        yield  # make it an async generator

    monkeypatch.setattr(chat.chat_graph, "astream", broken_astream)
    mock_request = AsyncMock()
    mock_request.is_disconnected.return_value = False

    state: ChatState = {
        "user_id": "emp_user",
        "role": "employee",
        "question": "error test",
        "history": [],
        "chunks": [],
        "sources": [],
        "answer_text": "",
        "status": "initialized",
    }

    events: list[str] = []
    async for ev in _stream_langgraph_sse(mock_request, state):
        events.append(ev)

    combined = "".join(events)
    assert "An error occurred while processing your request." in combined
    assert "secret credentials" not in combined
    assert "RuntimeError" not in combined
