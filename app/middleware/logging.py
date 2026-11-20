"""Structured JSON logging utilities for recording user interactions safely."""
from __future__ import annotations

import json
import logging
import re
import sys
from datetime import datetime, timezone
from typing import Any

from app.config import settings

_logger = logging.getLogger("chatbot.access")
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))
_logger.addHandler(_handler)
_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

# Patterns for scrubbing credentials from log strings
_SENSITIVE_URI_PATTERN = re.compile(r"://([^:]+):([^@]+)@")
_BEARER_TOKEN_PATTERN = re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
_API_KEY_PATTERN = re.compile(r"(gsk_|sk-|key-)[A-Za-z0-9_-]{16,}")


def sanitize_log_message(msg: str) -> str:
    """Scans and masks credentials, connection strings, and tokens from log messages."""
    cleaned = _SENSITIVE_URI_PATTERN.sub(r"://\1:***@", msg)
    cleaned = _BEARER_TOKEN_PATTERN.sub("Bearer ***", cleaned)
    return _API_KEY_PATTERN.sub(r"\1***", cleaned)


def log_interaction(
    user_id: str,
    question: str,
    sources: list[str],
    latency_ms: float,
) -> None:
    """Emits structured JSON log containing interaction metadata without secrets."""
    record = {
        "event": "chat_completed",
        "user_id": user_id,
        "question": question[:200],  # truncated for safety and brevity
        "sources": sources,
        "latency_ms": round(latency_ms, 2),
    }
    _logger.info(json.dumps(record))


def log_stream_event(
    event_type: str,
    request_id: str,
    user_id: str = "",
    session_id: str = "",
    stage: str = "",
    error_code: str = "",
    latency_ms: float | None = None,
) -> None:
    """Emits structured JSON log for SSE lifecycle events without secrets."""
    record: dict[str, Any] = {
        "event": event_type,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
    }
    if user_id:
        record["user_id"] = user_id
    if session_id:
        record["session_id"] = session_id
    if stage:
        record["stage"] = stage
    if error_code:
        record["error_code"] = error_code
    if latency_ms is not None:
        record["latency_ms"] = round(latency_ms, 2)
    _logger.info(json.dumps(record))

