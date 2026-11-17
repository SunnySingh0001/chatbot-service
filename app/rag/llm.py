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

def _stream_groq(messages: list[dict[str, str]]) -> Generator[str, None, None]:
    """Streams tokens from Groq's chat completions API."""
    from groq import Groq  # noqa: PLC0415

    client = Groq(api_key=settings.groq_api_key)
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

