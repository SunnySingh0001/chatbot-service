"""Pipeline node implementations for LangGraph orchestration."""
from __future__ import annotations

import logging
import time
from typing import Any

from langgraph.types import StreamWriter

from app.config import settings
from app.middleware.logging import log_interaction
from app.orchestration.state import ChatState
from app.rag.llm import stream_llm
from app.rag.prompt import FALLBACK_RESPONSES, build_messages, get_no_context_message
from app.rag.retriever import ROLE_ACCESS, retrieve

_logger = logging.getLogger("chatbot.orchestration.nodes")

FALLBACK_MESSAGE: str = FALLBACK_RESPONSES[0]


def authorize_node(state: ChatState) -> dict[str, Any]:
    """Validates user role against allowed access roles and marks status as authorized or denied."""
    role = state.get("role", "")
    user_id = state.get("user_id", "unknown")

    if not role or role not in ROLE_ACCESS:
        _logger.warning("Access denied: invalid or missing role %r for user %r", role, user_id)
        return {"status": "denied"}

    return {"status": "authorized"}


def condense_query_node(state: ChatState) -> dict[str, Any]:
    """Condenses follow-up question into standalone search query if rewriting is active."""
    from app.rag.condense import condense_query  # noqa: PLC0415

    q = state.get("question", "")
    hist = state.get("history", [])
    search_q = condense_query(q, hist)
    return {"search_query": search_q}


def retrieve_node(state: ChatState) -> dict[str, Any]:
    """Retrieves document chunks using role-filtered query, skipping on denied access."""
    if state.get("status") == "denied" or not state.get("role"):
        return {"chunks": [], "sources": []}

    orig_q = state.get("question", "")
    q = state.get("search_query") or orig_q
    try:
        chunks = retrieve(q, role=state["role"], original_query=orig_q)
    except Exception as exc:
        _logger.error("Retrieval error encountered (fail-closed): %s", type(exc).__name__)
        return {"chunks": [], "sources": []}

    sources = list(dict.fromkeys(c.source for c in chunks))
    return {"chunks": chunks, "sources": sources}


def route_after_retrieve(state: ChatState) -> str:
    """Routes to no_context if chunks are empty or access denied; otherwise routes to generate."""
    if state.get("status") == "denied" or not state.get("chunks"):
        return "no_context"
    return "generate"


def no_context_node(state: ChatState, writer: StreamWriter = lambda _: None) -> dict[str, Any]:
    """Sets standard fallback message or greeting without making any external LLM call."""
    msg = get_no_context_message(state.get("question", ""))
    writer({"token": msg})
    return {"answer_text": msg, "status": "no_context"}


def generate_node(state: ChatState, writer: StreamWriter = lambda _: None) -> dict[str, Any]:
    """Streams answer tokens from LLM gateway using prompt builder with escaped context."""
    history = state.get("history") or []
    messages = build_messages(state["question"], state["chunks"], history)

    token_count = 0
    collected_tokens: list[str] = []
    for token in stream_llm(messages):
        if token_count >= settings.max_output_tokens:
            break
        token_count += 1
        writer({"token": token})
        collected_tokens.append(token)

    return {"answer_text": "".join(collected_tokens), "status": "generated"}


def finalize_node(state: ChatState) -> dict[str, Any]:
    """Extracts sources strictly from retrieved chunks and logs structured telemetry."""
    chunks = state.get("chunks") or []
    sources = list(dict.fromkeys(c.source for c in chunks))

    start_time = state.get("start_time") or time.perf_counter()
    latency_ms = (time.perf_counter() - start_time) * 1000.0

    log_interaction(
        state.get("user_id", "unknown"),
        state.get("question", ""),
        sources,
        latency_ms,
    )
    return {"sources": sources, "status": "completed"}
