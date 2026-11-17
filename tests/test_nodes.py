"""Unit tests for LangGraph pipeline nodes, edges, role security, and execution."""
from __future__ import annotations

import logging
import pytest

from app.config import settings
from app.orchestration.graph import build_chat_graph, execute_graph
from app.orchestration.nodes import (
    FALLBACK_MESSAGE,
    authorize_node,
    finalize_node,
    generate_node,
    no_context_node,
    retrieve_node,
    route_after_retrieve,
)
from app.orchestration.state import ChatState
from app.rag.retriever import RetrievedChunk


@pytest.fixture(autouse=True)
def force_mock_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enforces mock LLM provider across all node unit tests."""
    monkeypatch.setattr(settings, "llm_provider", "mock")


def test_authorize_node_valid_roles() -> None:
    """Verifies that employee and hr roles are successfully authorized."""
    emp_state: ChatState = {"user_id": "u1", "role": "employee"}
    assert authorize_node(emp_state) == {"status": "authorized"}

    hr_state: ChatState = {"user_id": "u2", "role": "hr"}
    assert authorize_node(hr_state) == {"status": "authorized"}


def test_authorize_node_unknown_role_denies(caplog: pytest.LogCaptureFixture) -> None:
    """Verifies that an unknown role sets status=denied and emits a warning log."""
    with caplog.at_level(logging.WARNING):
        state: ChatState = {"user_id": "u_bad", "role": "contractor"}
        result = authorize_node(state)

    assert result == {"status": "denied"}
    assert "Access denied: invalid or missing role 'contractor'" in caplog.text


def test_authorize_node_missing_role_denies() -> None:
    """Verifies that missing or empty role sets status=denied."""
    state: ChatState = {"user_id": "u_anon", "role": ""}
    assert authorize_node(state) == {"status": "denied"}


def test_retrieve_node_denied_status_never_queries_vector_store() -> None:
    """Verifies that status=denied bypasses vector store and returns empty chunks."""
    state: ChatState = {
        "status": "denied",
        "role": "employee",
        "question": "What is the leave policy?",
    }
    result = retrieve_node(state)
    assert result == {"chunks": [], "sources": []}


def test_retrieve_node_missing_role_returns_empty() -> None:
    """Verifies that missing role returns empty chunks without querying vector store."""
    state: ChatState = {
        "status": "authorized",
        "role": "",
        "question": "What is the leave policy?",
    }
    result = retrieve_node(state)
    assert result == {"chunks": [], "sources": []}


def test_employee_never_gets_hr_chunks() -> None:
    """Verifies that employee query for confidential salary data never returns hr_benefits.md."""
    state: ChatState = {
        "status": "authorized",
        "role": "employee",
        "question": "What are the salary bands for engineering?",
    }
    result = retrieve_node(state)
    sources = result["sources"]
    assert "hr_benefits.md" not in sources


def test_hr_gets_both_chunks() -> None:
    """Verifies that an HR user can retrieve confidential HR chunks."""
    state: ChatState = {
        "status": "authorized",
        "role": "hr",
        "question": "What are the salary bands for engineering?",
    }
    result = retrieve_node(state)
    sources = result["sources"]
    assert "hr_benefits.md" in sources


def test_route_after_retrieve_no_chunks_goes_to_no_context() -> None:
    """Verifies conditional edge routes to no_context when chunks list is empty."""
    state: ChatState = {"status": "authorized", "chunks": []}
    assert route_after_retrieve(state) == "no_context"


def test_route_after_retrieve_denied_status_goes_to_no_context() -> None:
    """Verifies conditional edge routes to no_context when status is denied."""
    dummy_chunk = RetrievedChunk("text", "doc.md", "dept", "all", 0.9, 0)
    state: ChatState = {"status": "denied", "chunks": [dummy_chunk]}
    assert route_after_retrieve(state) == "no_context"


def test_route_after_retrieve_with_chunks_goes_to_generate() -> None:
    """Verifies conditional edge routes to generate when valid chunks exist."""
    dummy_chunk = RetrievedChunk("text", "doc.md", "dept", "all", 0.9, 0)
    state: ChatState = {"status": "authorized", "chunks": [dummy_chunk]}
    assert route_after_retrieve(state) == "generate"


def test_no_context_node_sets_fallback_without_llm_call(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies no_context sets the standard fallback message without calling stream_llm."""
    def fake_llm(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("LLM must not be called on no_context path")

    monkeypatch.setattr("app.orchestration.nodes.stream_llm", fake_llm)

    state: ChatState = {"question": "unanswerable question"}
    result = no_context_node(state)
    from app.rag.prompt import FALLBACK_RESPONSES, GREETING_RESPONSES  # noqa: PLC0415
    assert result["answer_text"] in FALLBACK_RESPONSES
    assert result["status"] == "no_context"


def test_no_context_node_greeting_response() -> None:
    """Verifies no_context returns a friendly greeting for greetings without vector search."""
    state: ChatState = {"question": "hellow"}
    result = no_context_node(state)
    from app.rag.prompt import GREETING_RESPONSES  # noqa: PLC0415
    assert result["answer_text"] in GREETING_RESPONSES
    assert result["status"] == "no_context"


def test_generate_node_streams_tokens() -> None:
    """Verifies generate_node constructs prompt and streams response tokens."""
    dummy_chunk = RetrievedChunk(
        "Welcome to Acme Corp. Full-time employees have 15 leaves.",
        "employee_handbook.md",
        "hr",
        "all",
        0.95,
        0,
    )
    state: ChatState = {
        "question": "company name",
        "chunks": [dummy_chunk],
        "history": [],
    }
    emitted = []
    writer = lambda chunk: emitted.append(chunk)

    result = generate_node(state, writer=writer)
    assert len(emitted) > 0
    assert result["status"] == "generated"
    assert len(result["answer_text"]) > 0


def test_finalize_node_sources_empty_when_no_chunks() -> None:
    """Verifies finalize_node sets sources=[] when chunks list is empty."""
    state: ChatState = {
        "user_id": "u1",
        "question": "cricket scores",
        "chunks": [],
    }
    result = finalize_node(state)
    assert result["sources"] == []
    assert result["status"] == "completed"


def test_finalize_node_extracts_sources_from_chunks() -> None:
    """Verifies finalize_node extracts unique source filenames from chunks."""
    c1 = RetrievedChunk("text 1", "doc_a.md", "gen", "all", 0.9, 0)
    c2 = RetrievedChunk("text 2", "doc_a.md", "gen", "all", 0.8, 1)
    c3 = RetrievedChunk("text 3", "doc_b.md", "gen", "all", 0.85, 0)

    state: ChatState = {
        "user_id": "u1",
        "question": "test question",
        "chunks": [c1, c2, c3],
    }
    result = finalize_node(state)
    assert result["sources"] == ["doc_a.md", "doc_b.md"]


def test_full_graph_execution_denied_role_path() -> None:
    """Verifies that an unknown role executes cleanly through authorize -> retrieve -> no_context -> finalize."""
    initial_state: ChatState = {
        "user_id": "intruder",
        "role": "unrecognized_role",
        "question": "Give me company secrets",
    }
    result = execute_graph(initial_state)
    assert result["status"] == "completed"
    from app.rag.prompt import FALLBACK_RESPONSES  # noqa: PLC0415
    assert result["answer_text"] in FALLBACK_RESPONSES
    assert result["sources"] == []


def test_full_graph_execution_valid_employee_path() -> None:
    """Verifies that an authorized employee executes through retrieve -> generate -> finalize."""
    initial_state: ChatState = {
        "user_id": "emp_01",
        "role": "employee",
        "question": "company name",
    }
    result = execute_graph(initial_state)
    assert result["status"] == "completed"
    assert len(result["sources"]) > 0
    assert "employee_handbook.md" in result["sources"]
    assert len(result["answer_text"]) > 0
