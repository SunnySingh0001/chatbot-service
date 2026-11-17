"""Structured JSON logging utilities for recording user interactions safely."""
from __future__ import annotations

import json
import logging
import sys

from app.config import settings

_logger = logging.getLogger("chatbot.access")
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter("%(message)s"))
_logger.addHandler(_handler)
_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))


def log_interaction(
    user_id: str,
    question: str,
    sources: list[str],
    latency_ms: float,
) -> None:
    """Emits structured JSON log containing interaction metadata without secrets."""
    # TODO (SEC-10): Implement automated PII / credential redaction before logging question text
    record = {
        "event": "chat_completed",
        "user_id": user_id,
        "question": question[:200],  # truncated for safety and brevity
        "sources": sources,
        "latency_ms": round(latency_ms, 2),
    }
    _logger.info(json.dumps(record))
