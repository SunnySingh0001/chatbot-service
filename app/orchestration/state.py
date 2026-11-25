"""Typed state container for LangGraph orchestration pipeline."""
from __future__ import annotations

from typing import Any, TypedDict

from app.rag.retriever import RetrievedChunk


class ChatState(TypedDict, total=False):
    """In-memory typed state dictionary flowing through LangGraph pipeline nodes."""

    user_id: str
    role: str
    client_id: str | None
    company_id: str | None
    user_identity: dict[str, str]
    session_id: str
    user_message_id: str
    question: str
    search_query: str
    history: list[dict[str, str]]
    history_summary: str | None
    entity_context: dict[str, Any]
    chunks: list[RetrievedChunk]
    sources: list[str]
    answer_text: str
    status: str
    start_time: float
    validation_status: str
    retry_count: int
    validated_citations: list[str]
    continuation_available: bool
    truncated: bool
    temporary: bool
