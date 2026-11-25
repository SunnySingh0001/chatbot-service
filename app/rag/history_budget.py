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
DEFAULT_CONTEXT_TOKEN_LIMIT: int = 5500

# Model-aware context window & safe output token caps: (context_window, recommended_max_output)
MODEL_TOKEN_PROFILES: dict[str, tuple[int, int]] = {
    "openai/gpt-oss-20b": (131072, 4096),
    "openai/gpt-oss-120b": (131072, 4096),
    "llama-3.3-70b-versatile": (128000, 4096),
    "llama-3.1-8b-instant": (128000, 2048),
    "gemini-3.5-flash": (1000000, 8192),
    "gemini-3-flash-preview": (1000000, 8192),
    "gemini-2.5-flash": (1000000, 8192),
    "gemini-flash-latest": (1000000, 8192),
    "gpt-4o": (128000, 4096),
    "gpt-4o-mini": (128000, 4096),
    "gpt-3.5-turbo": (16385, 2048),
}


def get_model_token_profile(
    provider: str | None = None,
    model: str | None = None,
) -> tuple[int, int]:
    """Resolves (context_window, max_output_tokens) for the active provider and model."""
    active_model = (model or settings.llm_model or "").strip().lower()
    active_prov = (provider or settings.llm_provider or "").strip().lower()
    if active_model in MODEL_TOKEN_PROFILES:
        win, model_out = MODEL_TOKEN_PROFILES[active_model]
        return win, min(settings.max_output_tokens, model_out) if settings.max_output_tokens > 0 else model_out
    if active_prov == "gemini":
        return 1000000, min(max(settings.max_output_tokens, 500), 8192)
    if active_prov in ("groq", "openai"):
        return 32768, min(max(settings.max_output_tokens, 500), 4096)
    return DEFAULT_MAX_CONTEXT_WINDOW, min(settings.max_output_tokens, 1024)


def compute_dynamic_token_budgets(
    history: list[dict[str, Any]] | None = None,
    system_prompt: str = "",
    entity_data: str = "",
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, int]:
    """Allocates non-overflowing dynamic budgets for system, entity, history, chunks, and output."""
    context_window, out_tokens = get_model_token_profile(provider, model)
    sys_tokens = max(DEFAULT_SYSTEM_TOKEN_RESERVE, estimate_tokens(system_prompt)) if system_prompt else DEFAULT_SYSTEM_TOKEN_RESERVE
    ent_tokens = estimate_tokens(entity_data) if entity_data else 0
    safe_margin = 256
    available = max(600, context_window - sys_tokens - out_tokens - ent_tokens - safe_margin)
    hist_budget = min(DEFAULT_MAX_HISTORY_TOKENS, max(200, int(available * 0.25)))
    chunk_budget = max(400, min(DEFAULT_CONTEXT_TOKEN_LIMIT * 2, available - hist_budget))
    return {
        "context_window": context_window,
        "output_budget": out_tokens,
        "system_budget": sys_tokens,
        "entity_budget": ent_tokens,
        "history_budget": hist_budget,
        "chunk_budget": chunk_budget,
    }


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


def fit_chunks_to_token_budget(
    chunks: list[Any],  # list[RetrievedChunk] without circular import
    max_context_tokens: int | None = None,
    system_reserve: int = 900,
    output_reserve: int | None = None,
) -> tuple[list[Any], dict[str, int | bool]]:
    """Packs retrieved chunks within model-aware dynamic token budget and appends notice if truncated."""
    dyn = compute_dynamic_token_budgets()
    cap = max_context_tokens if max_context_tokens is not None else max(DEFAULT_CONTEXT_TOKEN_LIMIT, dyn["chunk_budget"])
    if not chunks:
        return [], {"estimated_tokens": 0, "budget_tokens": cap, "omitted_chunks": 0, "truncated": False}

    out_res = output_reserve if output_reserve is not None else dyn["output_budget"]
    effective_budget = max(800, cap - min(system_reserve + out_res, max(800, cap // 2)))

    packed: list[Any] = []
    omitted_sources: list[str] = []
    used_tokens = 0
    for chunk in chunks:
        c_tokens = estimate_tokens(getattr(chunk, "text", ""))
        if used_tokens + c_tokens <= effective_budget or not packed:
            packed.append(chunk)
            used_tokens += c_tokens
        else:
            src = str(getattr(chunk, "source", "unknown"))
            if src not in omitted_sources:
                omitted_sources.append(src)

    truncated = bool(omitted_sources)
    if truncated and packed:
        first = packed[0]
        from app.rag.retriever import RetrievedChunk  # noqa: PLC0415

        packed.append(
            RetrievedChunk(
                text=(
                    f"[Token Budget Notice: {len(chunks) - len(packed)} additional context sections "
                    f"from {', '.join(omitted_sources)} exceeded single-turn LLM context budget. "
                    "Inform the user honestly that partial records are shown and suggest paginated batches ('Next') or CSV/JSON export.]"
                ),
                source=str(getattr(first, "source", "system")),
                department=str(getattr(first, "department", "general")),
                access_level=str(getattr(first, "access_level", "employee")),
                score=0.95,
                chunk_index=9999,
            )
        )
    metrics: dict[str, int | bool] = {
        "estimated_tokens": used_tokens,
        "budget_tokens": effective_budget,
        "omitted_chunks": max(0, len(chunks) - (len(packed) - (1 if truncated else 0))),
        "truncated": truncated,
    }
    return packed, metrics
