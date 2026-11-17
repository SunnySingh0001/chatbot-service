"""Typed state container for LangGraph orchestration pipeline."""
from __future__ import annotations

from typing import TypedDict

from app.rag.retriever import RetrievedChunk


class ChatState(TypedDict, total=False):
    """In-memory typed state dictionary flowing through LangGraph pipeline nodes."""

    user_id: str
    role: str
    question: str
    history: list[dict[str, str]]
    chunks: list[RetrievedChunk]
    sources: list[str]
    answer_text: str
    status: str
    start_time: float
