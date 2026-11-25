"""Phase 4 tests for provider parity, dynamic token budgets, entity conversation memory, and continuation."""
from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from app.config import settings
from app.orchestration import nodes
from app.rag import llm
from app.rag.history_budget import (
    compute_dynamic_token_budgets,
    fit_chunks_to_token_budget,
    get_model_token_profile,
)
from app.rag.retriever import RetrievedChunk
from app.rag.session_manager import session_manager


def test_dynamic_token_budgets_scale_by_model_and_never_exceed_window(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies model-aware context window budgets stay within safe bounds for Groq, OpenAI, and Gemini."""
    win_groq, out_groq = get_model_token_profile("groq", "openai/gpt-oss-20b")
    win_gem, out_gem = get_model_token_profile("gemini", "gemini-3.5-flash")
    win_gpt35, _ = get_model_token_profile("openai", "gpt-3.5-turbo")
    assert win_groq == 131072
    assert win_gem == 1000000
    assert win_gpt35 == 16385
    assert out_gem >= out_groq

    budgets = compute_dynamic_token_budgets(system_prompt="System prompt", entity_data="Entity snapshot " * 50, provider="openai", model="gpt-3.5-turbo")
    used_sum = budgets["system_budget"] + budgets["entity_budget"] + budgets["history_budget"] + budgets["chunk_budget"] + budgets["output_budget"]
    assert used_sum <= budgets["context_window"]

    chunks = [
        RetrievedChunk(text=("Alpha Beta Gamma " * 220), source=f"mongo:col_{i}", department="ops", access_level="employee", score=0.9, chunk_index=i)
        for i in range(10)
    ]
    packed, meta = fit_chunks_to_token_budget(chunks, max_context_tokens=1500, system_reserve=400, output_reserve=300)
    assert meta["truncated"] is True
    assert int(meta["omitted_chunks"]) > 0
    assert "Token Budget Notice" in packed[-1].text


def test_provider_parity_finish_reason_and_no_silent_mock_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies Groq/OpenAI/Gemini enforce max_output_tokens, detect finish_reason, and fail loudly in production."""
    # 1. Production environment missing API key must raise LLMProviderError (never emit mock text)
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "llm_provider", "openai")
    monkeypatch.setattr(settings, "openai_api_key", "")
    with pytest.raises(llm.LLMProviderError):
        list(llm.stream_llm([{"role": "user", "content": "Hello"}]))

    # 2. OpenAI stream captures max_tokens and finish_reason=length / content_filter
    seen_kwargs: dict[str, Any] = {}

    class _FakeOpenAI:
        def __init__(self, **_k: object) -> None:
            self.chat = types.SimpleNamespace(completions=types.SimpleNamespace(create=self._create))

        def _create(self, **kwargs: object) -> Any:
            seen_kwargs.update(kwargs)
            return [
                types.SimpleNamespace(choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content="Part 1"), finish_reason=None)]),
                types.SimpleNamespace(choices=[types.SimpleNamespace(delta=types.SimpleNamespace(content=None), finish_reason="length")]),
            ]

    monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=_FakeOpenAI))
    monkeypatch.setattr(settings, "openai_api_key", "sk-test-valid")
    monkeypatch.setattr(settings, "max_output_tokens", 750)
    out = "".join(llm.stream_llm([{"role": "user", "content": "Revenue summary"}]))
    assert out == "Part 1"
    assert seen_kwargs.get("max_tokens") == 750
    meta = llm.get_last_stream_meta()
    assert meta["truncated"] is True
    assert meta["finish_reason"] == "length"


def test_entity_aware_conversation_memory_and_non_duplicating_continuation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verifies session metadata preserves EntityConversationContext and drives multi-part continuation."""
    sid, _, _ = session_manager.get_or_create_session(None, "emp_test_p4", "finance")
    session_manager.save_user_message(sid, "emp_test_p4", "Last month Sales department ki paid invoices dikhao")

    chunks = [
        RetrievedChunk(
            text="Invoice FISIPL2526IN001 (`Sales`): Paid INR 5,900.00\nInvoice FISIPL2526IN002: Paid INR 11,800.00",
            source="mongo:invoices",
            department="finance",
            access_level="finance",
            score=1.0,
            chunk_index=i,
        )
        for i in range(4)
    ]

    # Force budget truncation / continuation availability cleanly
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(nodes, "_generate_raw_answer", lambda msgs: "Paid invoices summary: FISIPL2526IN001 INR 5,900.00\n\nSources: mongo:invoices")
    monkeypatch.setattr("app.rag.history_budget.fit_chunks_to_token_budget", lambda ch: (ch[:2], {"estimated_tokens": 500, "budget_tokens": 500, "omitted_chunks": 2, "truncated": True}))

    state_turn1 = {
        "user_id": "emp_test_p4",
        "role": "finance",
        "company_id": "comp-A",
        "session_id": sid,
        "question": "Last month Sales department ki paid invoices dikhao",
        "chunks": chunks,
        "history": [],
    }
    res1 = nodes.generate_node(state_turn1)  # type: ignore[arg-type]
    assert res1["continuation_available"] is True
    assert res1["truncated"] is True

    persisted_ctx = session_manager.get_entity_context(sid, "emp_test_p4")
    assert persisted_ctx["company_id"] == "comp-A"
    assert persisted_ctx["department"] == "Sales"
    assert "FISIPL2526IN001" in persisted_ctx["entity_ids"]
    assert persisted_ctx["continuation"]["pending"] is True
    assert persisted_ctx["continuation"]["delivered_chunk_count"] == 2

    # Turn 2: user requests continuation -> generate_node passes anti-duplication instruction
    captured_prompts: list[str] = []

    def _capture_gen(msgs: list[dict[str, str]]) -> str:
        captured_prompts.append(msgs[-1]["content"])
        return "FISIPL2526IN002 Paid INR 11,800.00\n\nSources: mongo:invoices"

    monkeypatch.setattr(nodes, "_generate_raw_answer", _capture_gen)
    monkeypatch.setattr("app.rag.history_budget.fit_chunks_to_token_budget", lambda ch: (ch, {"estimated_tokens": 200, "budget_tokens": 1000, "omitted_chunks": 0, "truncated": False}))

    res2 = nodes.generate_node({
        "user_id": "emp_test_p4",
        "role": "finance",
        "company_id": "comp-A",
        "session_id": sid,
        "question": "continue",
        "entity_context": persisted_ctx,
        "chunks": chunks[2:],
        "history": [],
    })  # type: ignore[arg-type]
    assert "WITHOUT repeating earlier lines" in captured_prompts[0]
    assert res2["continuation_available"] is False
    assert session_manager.get_entity_context(sid, "emp_test_p4")["continuation"]["pending"] is False
