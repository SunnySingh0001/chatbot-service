"""Typed state container for LangGraph orchestration pipeline."""
from __future__ import annotations

from typing import TypedDict

from app.rag.retriever import RetrievedChunk


class ChatState(TypedDict, total=False):
    """In-memory typed state dictionary flowing through LangGraph pipeline nodes."""

    user_id: str
    role: str
    session_id: str
    user_message_id: str
    question: str
    search_query: str
    history: list[dict[str, str]]
    history_summary: str | None
    chunks: list[RetrievedChunk]
    sources: list[str]
    answer_text: str
    status: str
    start_time: float
    validation_status: str
    retry_count: int
    validated_citations: list[str]
