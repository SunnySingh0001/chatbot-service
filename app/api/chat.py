"""Chat endpoint providing role-filtered RAG answers via Server-Sent Events."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, StreamingResponse

from app.auth.jwt import get_current_user
from app.config import settings
from app.middleware.logging import log_stream_event
from app.middleware.rate_limit import rate_limit_guard
from app.models.schemas import AuthenticatedUser, ChatRequest
from app.orchestration.graph import chat_graph
from app.orchestration.state import ChatState

_logger = logging.getLogger("chatbot.api.chat")
router = APIRouter(tags=["Chat"])

_SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _classify_stream_exception(exc: Exception) -> tuple[str, str]:
    """Classifies streaming exceptions into safe standardized error codes and messages."""
    exc_type = type(exc).__name__
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)) or "timeout" in exc_type.lower():
        return "LLM_TIMEOUT", "The request timed out. Please try again later."
    if "mongo" in exc_type.lower() or "database" in exc_type.lower():
        return "DATABASE_ERROR", "A database error occurred. Please try again later."
    return "INTERNAL_STREAM_ERROR", "An error occurred while processing your request. Please try again later."


async def _emit_sse_error(
    session_id: str,
    request_id: str,
    code: str,
    msg: str,
) -> AsyncGenerator[str, None]:
    """Yields safe SSE error event, token fallback, and terminal done event."""
    err_event = {
        "error": True,
        "code": code,
        "message": msg,
        "request_id": request_id,
    }
    yield f"data: {json.dumps(err_event)}\n\n"
    yield f"data: {json.dumps({'token': 'An error occurred while processing your request. Please try again later.'})}\n\n"
    done_payload: dict[str, Any] = {
        "done": True,
        "sources": [],
        "error": True,
        "request_id": request_id,
    }
    if session_id:
        done_payload["session_id"] = session_id
    yield f"data: {json.dumps(done_payload)}\n\n"


async def _stream_langgraph_sse(
    request: Request,
    initial_state: ChatState,
    request_id: str = "",
) -> AsyncGenerator[str, None]:
    """Streams LangGraph pipeline tokens and final sources with session ID as SSE events."""
    sources: list[str] = []
    session_id = initial_state.get("session_id", "")
    req_id = request_id or f"req-{uuid.uuid4().hex[:12]}"
    done_emitted = False
    continuation_available = False
    truncated = False

    try:
        async for mode, payload in chat_graph.astream(
            initial_state,
            stream_mode=["custom", "updates"],
            config={"recursion_limit": 10},
        ):
            if await request.is_disconnected():
                _logger.info("Client disconnected; aborting LangGraph stream [request_id=%s].", req_id)
                return

            if mode == "custom" and isinstance(payload, dict) and "token" in payload:
                yield f"data: {json.dumps({'token': payload['token']})}\n\n"
            elif mode == "updates" and isinstance(payload, dict):
                if "generate" in payload and isinstance(payload["generate"], dict):
                    continuation_available = bool(payload["generate"].get("continuation_available", continuation_available))
                    truncated = bool(payload["generate"].get("truncated", truncated))
                if "finalize" in payload and "sources" in payload["finalize"]:
                    sources = payload["finalize"]["sources"]
                elif "retrieve" in payload and "sources" in payload["retrieve"]:
                    sources = payload["retrieve"]["sources"]

        if not await request.is_disconnected():
            done_payload: dict[str, Any] = {
                "done": True,
                "sources": sources,
                "truncated": truncated,
                "continuation_available": continuation_available,
            }
            if initial_state.get("temporary"):
                done_payload["temporary"] = True
            elif session_id:
                done_payload["session_id"] = session_id
            done_emitted = True
            yield f"data: {json.dumps(done_payload)}\n\n"

    except asyncio.CancelledError:
        _logger.info("Stream cancelled by task cancellation or disconnect [request_id=%s].", req_id)
        raise

    except (ConnectionResetError, BrokenPipeError):
        _logger.info("Client connection closed during stream delivery [request_id=%s].", req_id)
        return

    except Exception as exc:
        if not await request.is_disconnected() and not done_emitted:
            code, msg = _classify_stream_exception(exc)
            _logger.error(
                "Error during LangGraph streaming [request_id=%s, code=%s]: %s",
                req_id,
                code,
                type(exc).__name__,
            )
            done_emitted = True
            async for ev in _emit_sse_error(session_id, req_id, code, msg):
                yield ev


def _run_langgraph_chat(
    request: Request,
    chat_req: ChatRequest,
    current_user: AuthenticatedUser,
    start_time: float,
    request_id: str,
) -> StreamingResponse:
    """Resolves session or stateless temporary mode, budgets history, and streams response."""
    from app.rag.history_budget import budget_conversation_history  # noqa: PLC0415
    from app.rag.session_manager import session_manager  # noqa: PLC0415

    if chat_req.temporary:
        sid = ""
        summary = None
        user_msg_id = ""
        raw_history = [m.model_dump() for m in chat_req.history]
    else:
        sid, server_msgs, summary = session_manager.get_or_create_session(
            chat_req.session_id, current_user.user_id, current_user.role
        )
        if chat_req.session_id:
            raw_history = [{"role": m["role"], "content": m["content"]} for m in server_msgs]
        elif server_msgs:
            raw_history = [{"role": m["role"], "content": m["content"]} for m in server_msgs]
        else:
            raw_history = [m.model_dump() for m in chat_req.history]

    budgeted_history, calc_summary = budget_conversation_history(raw_history)
    if not chat_req.temporary:
        user_msg_id = session_manager.save_user_message(sid, current_user.user_id, chat_req.message)
        entity_ctx = session_manager.get_entity_context(sid, current_user.user_id)
    else:
        entity_ctx = {}

    initial_state: ChatState = {
        "user_id": current_user.user_id,
        "role": current_user.role,
        "client_id": current_user.client_id,
        "company_id": current_user.company_id,
        "session_id": sid,
        "user_message_id": user_msg_id,
        "question": chat_req.message,
        "history": budgeted_history,
        "history_summary": summary or calc_summary,
        "entity_context": entity_ctx,
        "chunks": [],
        "sources": [],
        "answer_text": "",
        "status": "initialized",
        "start_time": start_time,
        "temporary": chat_req.temporary,
    }

    headers = dict(_SSE_HEADERS)
    if chat_req.temporary:
        headers["X-Temporary-Chat"] = "true"
    else:
        headers["X-Session-ID"] = sid
    headers["X-Request-ID"] = request_id
    return StreamingResponse(
        _stream_langgraph_sse(request, initial_state, request_id=request_id),
        media_type="text/event-stream",
        headers=headers,
    )


@router.post("/chat", dependencies=[Depends(rate_limit_guard)])
def chat(
    request: Request,
    chat_req: ChatRequest,
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> Response:
    """Streams role-filtered RAG response tokens via SSE using the LangGraph orchestrator."""
    start_time = time.perf_counter()
    req_id = f"req-{uuid.uuid4().hex[:12]}"

    if len(chat_req.message) > settings.max_message_length:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Message exceeds maximum allowed length of {settings.max_message_length}",
        )

    try:
        return _run_langgraph_chat(request, chat_req, current_user, start_time, req_id)
    except HTTPException:
        raise
    except Exception as exc:
        _logger.error(
            "Pre-stream internal error [request_id=%s, user_id=%s]: %s",
            req_id,
            current_user.user_id,
            type(exc).__name__,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": True,
                "code": "INTERNAL_ERROR",
                "message": "Something went wrong. Please try again.",
                "request_id": req_id,
                "detail": "Something went wrong. Please try again.",
            },
        )
