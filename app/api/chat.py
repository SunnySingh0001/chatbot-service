"""Chat endpoint providing role-filtered RAG answers via Server-Sent Events."""
from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncGenerator, Generator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.auth.jwt import get_current_user
from app.config import settings
from app.middleware.logging import log_interaction
from app.middleware.rate_limit import rate_limit_guard
from app.models.schemas import AuthenticatedUser, ChatRequest
from app.orchestration.graph import chat_graph
from app.orchestration.state import ChatState
from app.rag.llm import stream_llm
from app.rag.prompt import build_messages, build_no_context_messages
from app.rag.retriever import retrieve

_logger = logging.getLogger("chatbot.api.chat")
router = APIRouter(tags=["Chat"])

_SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _stream_classic_sse(
    messages: list[dict[str, str]],
    sources: list[str],
    user_id: str,
    question: str,
    start_time: float,
) -> Generator[str, None, None]:
    """Streams LLM tokens as SSE events for classic orchestrator."""
    token_count = 0
    for token in stream_llm(messages):
        if token_count >= settings.max_output_tokens:
            break
        token_count += 1
        yield f"data: {json.dumps({'token': token})}\n\n"

    yield f"data: {json.dumps({'done': True, 'sources': sources})}\n\n"
    latency_ms = (time.perf_counter() - start_time) * 1000.0
    log_interaction(user_id, question, sources, latency_ms)


async def _stream_langgraph_sse(
    request: Request,
    initial_state: ChatState,
) -> AsyncGenerator[str, None]:
    """Streams LangGraph pipeline tokens and final sources as SSE events."""
    sources: list[str] = []
    try:
        async for mode, payload in chat_graph.astream(
            initial_state,
            stream_mode=["custom", "updates"],
            config={"recursion_limit": 10},
        ):
            if await request.is_disconnected():
                _logger.info("Client disconnected; aborting LangGraph stream.")
                break

            if mode == "custom" and isinstance(payload, dict) and "token" in payload:
                yield f"data: {json.dumps({'token': payload['token']})}\n\n"
            elif mode == "updates" and isinstance(payload, dict):
                if "finalize" in payload and "sources" in payload["finalize"]:
                    sources = payload["finalize"]["sources"]
                elif "retrieve" in payload and "sources" in payload["retrieve"]:
                    sources = payload["retrieve"]["sources"]

        if not await request.is_disconnected():
            yield f"data: {json.dumps({'done': True, 'sources': sources})}\n\n"
    except Exception as exc:
        if not await request.is_disconnected():
            _logger.error("Error during LangGraph streaming execution: %s", exc)
            yield f"data: {json.dumps({'token': 'An error occurred while processing your request. Please try again later.'})}\n\n"
            yield f"data: {json.dumps({'done': True, 'sources': []})}\n\n"


def _run_classic_chat(
    chat_req: ChatRequest,
    current_user: AuthenticatedUser,
    start_time: float,
) -> StreamingResponse:
    """Builds classic streaming response by retrieving and prompting directly."""
    chunks = retrieve(chat_req.message, role=current_user.role)
    unique_sources = list(dict.fromkeys(c.source for c in chunks))

    history_payload = [m.model_dump() for m in chat_req.history]
    if chunks:
        messages = build_messages(chat_req.message, chunks, history_payload)
    else:
        messages = build_no_context_messages(chat_req.message, history_payload)

    return StreamingResponse(
        _stream_classic_sse(
            messages=messages,
            sources=unique_sources,
            user_id=current_user.user_id,
            question=chat_req.message,
            start_time=start_time,
        ),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


def _run_langgraph_chat(
    request: Request,
    chat_req: ChatRequest,
    current_user: AuthenticatedUser,
    start_time: float,
) -> StreamingResponse:
    """Builds LangGraph streaming response running the orchestrated graph."""
    history_payload = [m.model_dump() for m in chat_req.history]
    initial_state: ChatState = {
        "user_id": current_user.user_id,
        "role": current_user.role,
        "question": chat_req.message,
        "history": history_payload,
        "chunks": [],
        "sources": [],
        "answer_text": "",
        "status": "initialized",
        "start_time": start_time,
    }
    return StreamingResponse(
        _stream_langgraph_sse(request, initial_state),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/chat", dependencies=[Depends(rate_limit_guard)])
def chat(
    request: Request,
    chat_req: ChatRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> StreamingResponse:
    """Streams role-filtered RAG response tokens via SSE using active orchestrator."""
    start_time = time.perf_counter()

    if len(chat_req.message) > settings.max_message_length:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Message exceeds maximum allowed length of {settings.max_message_length}",
        )

    if settings.orchestrator == "langgraph":
        return _run_langgraph_chat(request, chat_req, current_user, start_time)

    return _run_classic_chat(chat_req, current_user, start_time)
