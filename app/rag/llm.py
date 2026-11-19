"""
Single gateway for all LLM calls.
Provider, model, and API key come from environment variables only.
No other file in this project may call an LLM provider directly.
"""
from __future__ import annotations

from collections.abc import Generator

from app.config import settings


def _has_valid_key(key: str) -> bool:
    """Verifies that key is non-empty and not a default template placeholder."""
    return bool(key and not key.strip().startswith("your-"))


def get_active_llm_provider() -> str:
    """Returns effective active LLM provider; missing or placeholder keys return 'mock'."""
    raw = settings.llm_provider.lower().strip()
    if raw == "groq" and _has_valid_key(settings.groq_api_key):
        return "groq"
    if raw == "openai" and _has_valid_key(settings.openai_api_key):
        return "openai"
    if raw == "gemini" and _has_valid_key(settings.gemini_api_key):
        return "gemini"
    return "mock"


def complete_llm(
    messages: list[dict[str, str]],
    max_tokens: int = 80,
    temperature: float = 0.0,
    timeout: float = 8.0,
    model: str | None = None,
) -> str:
    """Executes a single non-streaming completion through the active LLM provider gateway."""
    active = get_active_llm_provider()
    if active == "groq":
        return _complete_groq(messages, max_tokens, temperature, timeout, model=model)
    if active == "openai":
        return _complete_openai(messages, max_tokens, temperature, timeout, model=model)
    if active == "gemini":
        return _complete_gemini(messages, max_tokens, temperature, timeout, model=model)
    return _complete_mock(messages)


def stream_llm(messages: list[dict[str, str]]) -> Generator[str, None, None]:
    """Routes to the configured provider and streams response tokens."""
    provider = settings.llm_provider.lower()
    
    # Development mode without credentials
    if provider == "mock":
        yield from _stream_mock(messages)
        return

    if provider == "groq":
        if not _has_valid_key(settings.groq_api_key):
            yield from _stream_mock(messages, fallback_notice="[No valid GROQ_API_KEY configured; running in mock dev mode]")
            return
        yield from _stream_groq(messages)
    elif provider == "openai":
        if not _has_valid_key(settings.openai_api_key):
            yield from _stream_mock(messages, fallback_notice="[No valid OPENAI_API_KEY configured; running in mock dev mode]")
            return
        yield from _stream_openai(messages)
    elif provider == "gemini":
        if not _has_valid_key(settings.gemini_api_key):
            yield from _stream_mock(messages, fallback_notice="[No valid GEMINI_API_KEY configured; running in mock dev mode]")
            return
        yield from _stream_gemini(messages)
    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER={provider!r}. "
            "Set LLM_PROVIDER to one of: groq | openai | gemini | mock"
        )


def _extract_doc_snippets(user_msg: str) -> list[str]:
    """Extracts relevant text snippets from retrieved context documents."""
    import re  # noqa: PLC0415

    doc_matches = re.findall(
        r'<document\s+source="([^"]+)"[^>]*>(.*?)</document>', user_msg, re.DOTALL
    )
    q_match = re.search(r'<question>(.*?)</question>', user_msg, re.DOTALL)
    q_text = q_match.group(1).strip() if q_match else ""
    q_words = {w.lower() for w in re.findall(r"\w+", q_text) if len(w) > 2}

    snippets: list[str] = []
    for _src, content in doc_matches:
        clean = content.replace("&lt;", "<").replace("&gt;", ">")
        lines = [
            l.strip()
            for l in clean.splitlines()
            if l.strip() and not l.strip().startswith("---")
        ]
        matched = [
            l.lstrip("#").strip()
            for l in lines
            if q_words & {w.lower() for w in re.findall(r"\w+", l)}
        ]
        if matched:
            snippets.extend(matched[:3])
        elif lines:
            snippets.extend([l.lstrip("#").strip() for l in lines if not l.startswith("#")][:2])
    return snippets[:4]


def _build_mock_response(user_msg: str) -> str:
    """Builds synthetic answer text from retrieved context documents or returns fallback."""
    import re  # noqa: PLC0415

    if "No relevant documents were found" in user_msg:
        from app.rag.prompt import get_no_context_message  # noqa: PLC0415

        q_match = re.search(r"<question>(.*?)</question>", user_msg, re.DOTALL)
        q_text = q_match.group(1).strip() if q_match else ""
        return get_no_context_message(q_text)

    sources = list(dict.fromkeys(re.findall(r'<document\s+source="([^"]+)"', user_msg)))
    snippets = _extract_doc_snippets(user_msg)
    response_text = "According to company documentation:\n"
    for item in snippets:
        response_text += f"- {item}\n"
    if sources:
        response_text += f"\nSources: {', '.join(sources)}"
    return response_text


def _stream_mock(
    messages: list[dict[str, str]],
    fallback_notice: str = "",
) -> Generator[str, None, None]:
    """Simulates LLM response token stream for local development without API keys."""
    import re  # noqa: PLC0415
    import time  # noqa: PLC0415

    delay_s = max(0, settings.mock_token_delay_ms) / 1000.0
    if fallback_notice:
        yield f"{fallback_notice}\n\n"

    user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    text = _build_mock_response(user_msg)
    for token in re.findall(r"\S+|\n|\s+", text):
        if delay_s > 0:
            time.sleep(delay_s)
        yield token


# ── Provider implementations ──────────────────────────────────────────────────
# Lazy imports so missing provider SDKs don't crash at startup.

def _get_groq_models(primary: str) -> list[str]:
    """Returns candidate Groq models in prioritized fallback order."""
    candidates = [primary, "openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
    seen: set[str] = set()
    result: list[str] = []
    for m in candidates:
        if m and m not in seen:
            seen.add(m)
            result.append(m)
    return result


def _stream_groq(messages: list[dict[str, str]]) -> Generator[str, None, None]:
    """Streams tokens from Groq API with automatic fallback on rate limits."""
    from groq import APIStatusError, Groq, RateLimitError  # noqa: PLC0415

    client = Groq(api_key=settings.groq_api_key)
    models = _get_groq_models(settings.llm_model)
    last_err: Exception | None = None
    for model_name in models:
        try:
            stream = client.chat.completions.create(
                model=model_name,
                messages=messages,  # type: ignore[arg-type]
                temperature=settings.llm_temperature,
                stream=True,
            )
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    yield delta.content
            return
        except (RateLimitError, APIStatusError) as exc:
            last_err = exc
            continue
    if last_err:
        raise last_err


def _stream_openai(messages: list[dict[str, str]]) -> Generator[str, None, None]:
    """Streams tokens from OpenAI's chat completions API."""
    from openai import OpenAI  # noqa: PLC0415

    client = OpenAI(api_key=settings.openai_api_key)
    stream = client.chat.completions.create(
        model=settings.llm_model,
        messages=messages,  # type: ignore[arg-type]
        temperature=settings.llm_temperature,
        stream=True,
    )
    for chunk in stream:
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content


def _stream_gemini(messages: list[dict[str, str]]) -> Generator[str, None, None]:
    """Streams tokens from Google Gemini's generative API."""
    import google.generativeai as genai  # noqa: PLC0415

    genai.configure(api_key=settings.gemini_api_key)

    system_content = next(
        (m["content"] for m in messages if m["role"] == "system"), ""
    )
    non_system = [m for m in messages if m["role"] != "system"]

    model = genai.GenerativeModel(
        model_name=settings.llm_model,
        system_instruction=system_content,
        generation_config={"temperature": settings.llm_temperature},
    )

    # Convert history (all but last user message) to Gemini's format.
    gemini_history = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [m["content"]]}
        for m in non_system[:-1]
    ]
    last_user = non_system[-1]["content"] if non_system else ""

    chat = model.start_chat(history=gemini_history)
    response = chat.send_message(last_user, stream=True)
    for chunk in response:
        if chunk.text:
            yield chunk.text


def _complete_groq(
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    timeout: float,
    model: str | None = None,
) -> str:
    """Completes query rewrite via Groq API with fallback on rate limits."""
    from groq import APIStatusError, Groq, RateLimitError  # noqa: PLC0415

    client = Groq(api_key=settings.groq_api_key, timeout=timeout, max_retries=0)
    primary = model or settings.rewrite_model or settings.llm_model
    models = _get_groq_models(primary)
    last_err: Exception | None = None
    for model_name in models:
        try:
            kwargs: dict[str, object] = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
            }
            if model_name.startswith("openai/gpt-oss-"):
                kwargs["reasoning_effort"] = "low"
            resp = client.chat.completions.create(**kwargs)  # type: ignore[arg-type]
            return resp.choices[0].message.content or ""
        except (RateLimitError, APIStatusError) as exc:
            last_err = exc
            continue
    if last_err:
        raise last_err
    return ""


def _complete_openai(
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    timeout: float,
    model: str | None = None,
) -> str:
    """Completes query rewrite via OpenAI chat completions API."""
    from openai import OpenAI  # noqa: PLC0415

    client = OpenAI(api_key=settings.openai_api_key, timeout=timeout)
    target_model = model or settings.rewrite_model or settings.llm_model
    resp = client.chat.completions.create(
        model=target_model,
        messages=messages,  # type: ignore[arg-type]
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return resp.choices[0].message.content or ""


def _complete_gemini(
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    timeout: float,
    model: str | None = None,
) -> str:
    """Completes query rewrite via Google Gemini generative API."""
    import google.generativeai as genai  # noqa: PLC0415

    genai.configure(api_key=settings.gemini_api_key)
    target_model = model or settings.rewrite_model or settings.llm_model
    system_content = next((m["content"] for m in messages if m["role"] == "system"), "")
    non_system = [m for m in messages if m["role"] != "system"]
    gemini_model = genai.GenerativeModel(
        model_name=target_model,
        system_instruction=system_content if system_content else None,
        generation_config={"temperature": temperature, "max_output_tokens": max_tokens},
    )
    user_prompt = non_system[-1]["content"] if non_system else ""
    resp = gemini_model.generate_content(user_prompt, request_options={"timeout": timeout})
    return resp.text or ""


def _complete_mock(messages: list[dict[str, str]]) -> str:
    """Returns deterministic mock completion text."""
    user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    return user_msg


