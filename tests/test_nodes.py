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


def test_stream_gemini_does_not_concatenate_after_mid_stream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies mid-stream Gemini failure aborts instead of concatenating a fallback model response."""
    import sys
    import types
    from app.rag import llm  # noqa: PLC0415

    class _Chunk:
        def __init__(self, text: str | None = None, raise_val_err: bool = False) -> None:
            self._text = text
            self._raise = raise_val_err

        @property
        def text(self) -> str:
            if self._raise:
                raise ValueError("finish chunk has no parts")
            return self._text or ""

    class _FakeStreamWithMetaEnd:
        def __iter__(self):
            yield _Chunk("Row 1 ")
            yield _Chunk(raise_val_err=True)
            yield _Chunk("complete")

    class _FakeBrokenMidStream:
        def __iter__(self):
            yield _Chunk("13 EMP-1")
            raise RuntimeError("mid-stream disconnect")

    call_count = {"n": 0}

    class _FakeModel:
        def __init__(self, **_kwargs: object) -> None:
            call_count["n"] += 1

        def start_chat(self, **_kwargs: object):
            idx = call_count["n"]
            return types.SimpleNamespace(
                send_message=lambda *_a, **_k: _FakeStreamWithMetaEnd() if idx == 1 else _FakeBrokenMidStream()
            )

    fake_genai = types.SimpleNamespace(
        configure=lambda **_k: None,
        GenerativeModel=_FakeModel,
    )
    fake_google = types.ModuleType("google")
    fake_google.generativeai = fake_genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.generativeai", fake_genai)
    monkeypatch.setattr(llm.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(llm.settings, "llm_model", "gemini-flash-lite-latest")

    # 1. Trailing metadata chunk raising ValueError completes without restarting fallback model
    out = "".join(llm._stream_gemini([{"role": "user", "content": "hi"}]))
    assert out == "Row 1 complete"
    assert call_count["n"] == 1

    # 2. Broken mid-stream raises cleanly without invoking second fallback model
    call_count["n"] = 1  # Next call uses idx=2 broken stream
    with pytest.raises(RuntimeError, match="mid-stream disconnect"):
        list(llm._stream_gemini([{"role": "user", "content": "hi"}]))
    assert call_count["n"] == 2


def test_attempt_generation_discards_partial_output_on_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies generate retry discards interrupted partial chunks rather than concatenating answers."""
    from app.orchestration import nodes  # noqa: PLC0415

    attempts = {"count": 0}

    def flaky_stream(_messages: list[dict[str, str]]):
        attempts["count"] += 1
        if attempts["count"] == 1:
            yield "13 | Fatema Shabbir | EMP-1"
            raise RuntimeError("truncated mid-stream")
        yield "Clean complete directory response"

    monkeypatch.setattr(nodes, "stream_llm", flaky_stream)
    chunk = RetrievedChunk("Employee directory data", "mongo:users", "general", "all", 0.9, 0)
    val_res, retries = nodes._attempt_generation([{"role": "user", "content": "employees"}], [chunk], "employees")

    assert retries == 0
    assert attempts["count"] == 2
    assert val_res.cleaned_answer == "Clean complete directory response"
    assert "EMP-1" not in val_res.cleaned_answer


def test_retry_switches_to_fallback_model_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies mid-stream failure retry triggers skip_primary=True and skips failing primary model."""
    import sys
    import types
    from app.orchestration import nodes  # noqa: PLC0415
    from app.rag import llm  # noqa: PLC0415

    seen_models: list[str] = []

    class _Chunk:
        def __init__(self, text: str) -> None:
            self.text = text

    class _FakeModel:
        def __init__(self, model_name: str, **_kwargs: object) -> None:
            seen_models.append(model_name)
            self._name = model_name

        def start_chat(self, **_kwargs: object):
            def _send(*_a: object, **_k: object):
                if self._name == "gemini-3.5-flash":
                    yield _Chunk("Partial row 13")
                    raise RuntimeError("primary stream cut")
                yield _Chunk("Full clean fallback response")

            return types.SimpleNamespace(send_message=_send)

    fake_genai = types.SimpleNamespace(
        configure=lambda **_k: None,
        GenerativeModel=_FakeModel,
    )
    fake_google = types.ModuleType("google")
    fake_google.generativeai = fake_genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.generativeai", fake_genai)
    monkeypatch.setattr(llm.settings, "llm_provider", "gemini")
    monkeypatch.setattr(llm.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(llm.settings, "llm_model", "gemini-3.5-flash")
    monkeypatch.setattr(nodes, "stream_llm", llm.stream_llm)

    answer = nodes._generate_raw_answer([{"role": "user", "content": "list staff"}])
    assert answer == "Full clean fallback response"
    assert seen_models == ["gemini-3.5-flash", "gemini-3-flash-preview"]


def test_truncated_table_or_abnormal_finish_reason_triggers_clean_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies mid-row table cutoffs (like row 40 '**') and finish_reason=MAX_TOKENS trigger clean fallback."""
    import sys
    import types
    from app.orchestration import nodes  # noqa: PLC0415
    from app.rag import llm  # noqa: PLC0415

    assert nodes._is_truncated_response("| # | Name |\n|---|---|\n| 40 | **") is True
    assert nodes._is_truncated_response("| # | Name |\n|---|---|\n| 40 | **Zenab Husain** |") is False
    assert llm._is_abnormal_gemini_finish(2) is True
    assert llm._is_abnormal_gemini_finish(1) is False

    seen_models: list[str] = []

    class _FinChunk:
        def __init__(self, text: str, finish_reason: int | None = None) -> None:
            self.text = text
            self.candidates = [types.SimpleNamespace(finish_reason=finish_reason)] if finish_reason is not None else []

    class _FakeModel:
        def __init__(self, model_name: str, **_kwargs: object) -> None:
            seen_models.append(model_name)
            self._name = model_name

        def start_chat(self, **_kwargs: object):
            def _send(*_a: object, **_k: object):
                if self._name == "gemini-3.5-flash":
                    yield _FinChunk("| # | Name |\n|---|---|\n| 40 | **", finish_reason=2)
                else:
                    yield _FinChunk("| # | Name |\n|---|---|\n| 40 | **Zenab Husain** |", finish_reason=1)

            return types.SimpleNamespace(send_message=_send)

    fake_genai = types.SimpleNamespace(configure=lambda **_k: None, GenerativeModel=_FakeModel)
    fake_google = types.ModuleType("google")
    fake_google.generativeai = fake_genai  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.generativeai", fake_genai)
    monkeypatch.setattr(llm.settings, "llm_provider", "gemini")
    monkeypatch.setattr(llm.settings, "gemini_api_key", "test-key")
    monkeypatch.setattr(llm.settings, "llm_model", "gemini-3.5-flash")
    monkeypatch.setattr(nodes, "stream_llm", llm.stream_llm)

    answer = nodes._generate_raw_answer([{"role": "user", "content": "all 40 employees"}])
    assert "| 40 | **Zenab Husain** |" in answer
    assert seen_models == ["gemini-3.5-flash", "gemini-3-flash-preview"]



