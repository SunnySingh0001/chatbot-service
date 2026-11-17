"""Tests for orchestrator configuration, startup validation, /health endpoint, and skeleton graph."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings, settings
from app.main import app, _validate_orchestrator_on_startup
from app.orchestration.graph import build_chat_graph
from app.orchestration.state import ChatState

client = TestClient(app)


def test_default_orchestrator_is_classic() -> None:
    """Verifies that the default orchestrator mode is 'classic'."""
    assert settings.orchestrator == "classic"


def test_invalid_orchestrator_fails_validation() -> None:
    """Verifies that configuring an invalid orchestrator raises ValidationError."""
    with pytest.raises(ValidationError) as exc_info:
        Settings(orchestrator="unsupported_agent")
    assert "Must be either 'classic' or 'langgraph'" in str(exc_info.value)


def test_invalid_orchestrator_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that startup validation halts the application if orchestrator is invalid."""
    monkeypatch.setattr(settings, "orchestrator", "bad_orchestrator")
    with pytest.raises(RuntimeError) as exc_info:
        _validate_orchestrator_on_startup()
    assert "Invalid ORCHESTRATOR='bad_orchestrator'" in str(exc_info.value)


def test_health_shows_classic_orchestrator() -> None:
    """Verifies that GET /health displays orchestrator='classic' by default."""
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "1.0.0"
    assert data["orchestrator"] == "classic"


def test_health_shows_langgraph_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies that GET /health dynamically reflects orchestrator='langgraph'."""
    monkeypatch.setattr(settings, "orchestrator", "langgraph")
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["orchestrator"] == "langgraph"


def test_skeleton_graph_compiles_and_executes() -> None:
    """Verifies that the graph compiles and invokes through the pipeline."""
    graph = build_chat_graph()
    initial_state: ChatState = {
        "user_id": "test_user",
        "role": "employee",
        "question": "test question",
        "history": [],
        "chunks": [],
        "sources": [],
        "answer_text": "",
        "status": "initialized",
    }
    result = graph.invoke(initial_state)
    assert result["user_id"] == "test_user"
    assert result["status"] == "completed"
