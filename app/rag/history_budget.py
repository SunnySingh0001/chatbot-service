"""Token-aware context window estimation, history budgeting, and older turn summarization."""
from __future__ import annotations

import logging
from typing import Any

from app.config import settings

_logger = logging.getLogger("chatbot.rag.history_budget")

DEFAULT_SYSTEM_TOKEN_RESERVE: int = 350
DEFAULT_CONTEXT_TOKEN_RESERVE: int = 1200
DEFAULT_MAX_CONTEXT_WINDOW: int = 4096
DEFAULT_MAX_HISTORY_TOKENS: int = 1500


def estimate_tokens(text: str) -> int:
    """Conservatively estimates tokens using script-aware character ratios and a safety margin."""
    if not text:
        return 0
    devanagari_count = sum(1 for ch in text if "\u0900" <= ch <= "\u097f")
    other_count = len(text) - devanagari_count
    # Devanagari subwords average ~1.2 chars/token; Latin/English averages ~3.3 chars/token
    devanagari_tokens = (devanagari_count * 10) // 12
    latin_tokens = (other_count + 3) // 3
    return max(1, devanagari_tokens + latin_tokens)


def calculate_history_token_budget(
    context_window: int = DEFAULT_MAX_CONTEXT_WINDOW,
    system_reserve: int = DEFAULT_SYSTEM_TOKEN_RESERVE,
    context_reserve: int = DEFAULT_CONTEXT_TOKEN_RESERVE,
    output_reserve: int | None = None,
) -> int:
    """Calculates available token budget for history after reserving headroom for system, context, and output."""
    out_reserve = output_reserve if output_reserve is not None else settings.max_output_tokens
    budget = context_window - (system_reserve + context_reserve + out_reserve)
    return max(200, min(budget, DEFAULT_MAX_HISTORY_TOKENS))


def summarize_older_turns(older_turns: list[dict[str, Any]]) -> str:
    """Summarizes older conversation turns preserving key entities and decisions."""
    topics: list[str] = []
    for turn in older_turns:
        role = turn.get("role", "user")
        content = str(turn.get("content", "")).strip()
        if not content:
            continue
        first_line = content.splitlines()[0][:70].strip()
        topics.append(f"{role}: {first_line}")

    joined = "; ".join(topics[-4:])
    return f"Summary of earlier conversation: [{joined}]"


def budget_conversation_history(
    history: list[dict[str, Any]],
    max_tokens: int | None = None,
) -> tuple[list[dict[str, str]], str | None]:
    """Packs recent turns within token budget and summarizes older overflow turns without splitting messages."""
    if not history:
        return [], None

    budget = max_tokens if max_tokens is not None else calculate_history_token_budget()
    recent_turns: list[dict[str, str]] = []
    tokens_used = 0
    split_idx = 0

    # Walk backwards from most recent turn to pack whole turns
    for i in range(len(history) - 1, -1, -1):
        turn = history[i]
        content = str(turn.get("content", ""))
        role = str(turn.get("role", "user"))
        turn_tokens = estimate_tokens(content)

        if tokens_used + turn_tokens > budget and recent_turns:
            split_idx = i + 1
            break
        recent_turns.insert(0, {"role": role, "content": content})
        tokens_used += turn_tokens
    else:
        split_idx = 0

    older_turns = history[:split_idx]
    if not older_turns:
        return recent_turns, None

    summary = summarize_older_turns(older_turns)
    return recent_turns, summary
