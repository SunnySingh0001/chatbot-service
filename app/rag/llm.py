"""
Single gateway for all LLM calls.
Provider, model, and API key come from environment variables only.
No other file in this project may call an LLM provider directly.
"""
from __future__ import annotations

import contextvars
from collections.abc import Generator
from dataclasses import asdict, dataclass

from app.config import settings


class LLMProviderError(RuntimeError):
    """Raised on upstream LLM API errors, content filtering, or missing production keys."""


@dataclass
class LLMStreamMeta:
    """Tracks finish reason, truncation, content filtering, and active model limits per request."""

    provider: str = "mock"
    model: str = ""
    finish_reason: str | None = None
    truncated: bool = False
    content_filtered: bool = False
    max_output_tokens: int = 500


_STREAM_META_VAR: contextvars.ContextVar[LLMStreamMeta | None] = contextvars.ContextVar(
    "chatbot_llm_stream_meta", default=None
)


def get_last_stream_meta() -> dict[str, object]:
    """Returns metadata dict for the most recent stream_llm call in this execution context."""
    meta = _STREAM_META_VAR.get()
    return asdict(meta) if meta is not None else asdict(LLMStreamMeta())


def _is_production_env() -> bool:
    """Returns True strictly when APP_ENV indicates live production deployment."""
    return settings.app_env.strip().lower() in ("production", "prod")


def _resolved_max_output_tokens() -> int:
    """Returns provider-bounded output token ceiling cleanly above safety minimum."""
    return max(64, int(getattr(settings, "max_output_tokens", 500)))


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


def stream_llm(
    messages: list[dict[str, str]],
    skip_primary: bool = False,
) -> Generator[str, None, None]:
    """Routes to the configured provider, tracking finish reasons and forbidding silent production mocks."""
    provider = settings.llm_provider.lower()
    max_out = _resolved_max_output_tokens()
    _STREAM_META_VAR.set(LLMStreamMeta(provider=provider, model=settings.llm_model, max_output_tokens=max_out))

    if provider == "mock":
        yield from _stream_mock(messages)
        return

    if provider == "groq":
        if not _has_valid_key(settings.groq_api_key):
            if _is_production_env():
                raise LLMProviderError("GROQ_API_KEY is missing in production environment.")
            yield from _stream_mock(messages, fallback_notice="[No valid GROQ_API_KEY configured; running in mock dev mode]")
            return
        yield from _stream_groq(messages, skip_primary=skip_primary)
    elif provider == "openai":
        if not _has_valid_key(settings.openai_api_key):
            if _is_production_env():
                raise LLMProviderError("OPENAI_API_KEY is missing in production environment.")
            yield from _stream_mock(messages, fallback_notice="[No valid OPENAI_API_KEY configured; running in mock dev mode]")
            return
        yield from _stream_openai(messages)
    elif provider == "gemini":
        if not _has_valid_key(settings.gemini_api_key):
            if _is_production_env():
                raise LLMProviderError("GEMINI_API_KEY is missing in production environment.")
            yield from _stream_mock(messages, fallback_notice="[No valid GEMINI_API_KEY configured; running in mock dev mode]")
            return
        yield from _stream_gemini(messages, skip_primary=skip_primary)
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
    candidates = [primary, "qwen/qwen3.8-27b", "openai/gpt-oss-120b"]
    seen: set[str] = set()
    result: list[str] = []
    for m in candidates:
        if m and m not in seen:
            seen.add(m)
            result.append(m)
    return result


def _trim_oversized_payload(messages: list[dict[str, str]]) -> list[dict[str, str]]:
    """Trims oversized context payload slightly if provider returns payload or TPM limit error."""
    trimmed: list[dict[str, str]] = []
    for m in messages:
        if m.get("role") == "user" and "<context>" in m.get("content", ""):
            content = m["content"]
            start = content.find("<context>")
            end = content.find("</context>")
            if start != -1 and end != -1:
                ctx_body = content[start + 9 : end]
                docs = [d for d in ctx_body.split("</document>") if d.strip()]
                if len(docs) > 2:
                    keep_count = max(2, int(len(docs) * 0.80))
                    kept_docs = "</document>".join(docs[:keep_count]) + "</document>\n"
                    new_content = content[: start + 9] + "\n" + kept_docs + content[end:]
                    trimmed.append({"role": m["role"], "content": new_content})
                    continue
        trimmed.append(m)
    return trimmed


def _extract_gemini_chunk_text(chunk: object) -> str:
    """Extracts text from a Gemini stream chunk while ignoring metadata-only chunks safely."""
    try:
        text = getattr(chunk, "text", None)
        return text if isinstance(text, str) else ""
    except ValueError:
        return ""


def _record_finish_reason(model_name: str, fin: object) -> None:
    """Updates execution-local stream metadata with normalized finish_reason and truncation state."""
    meta = _STREAM_META_VAR.get() or LLMStreamMeta()
    meta.model = model_name
    if fin is None:
        _STREAM_META_VAR.set(meta)
        return
    fin_str = str(getattr(fin, "name", fin)).strip().lower()
    meta.finish_reason = fin_str
    if fin_str in ("length", "max_tokens", "2", "finishreason.max_tokens"):
        meta.truncated = True
    if fin_str in ("content_filter", "safety", "recitation", "3", "4", "finishreason.safety"):
        meta.content_filtered = True
    _STREAM_META_VAR.set(meta)


def _stream_single_groq_attempt(
    client: object,  # Groq SDK client instance
    model_name: str,
    attempt_msgs: list[dict[str, str]],
) -> Generator[str, None, None]:
    """Streams chunks from one Groq model attempt with max_tokens and finish_reason detection."""
    kwargs: dict[str, object] = {
        "model": model_name,
        "messages": attempt_msgs,
        "temperature": settings.llm_temperature,
        "max_tokens": _resolved_max_output_tokens(),
        "stream": True,
    }
    if model_name.startswith("openai/gpt-oss-"):
        kwargs["reasoning_effort"] = "low"
    stream = client.chat.completions.create(**kwargs)  # type: ignore[attr-defined]
    for chunk in stream:
        choice = chunk.choices[0]
        if getattr(choice, "finish_reason", None) is not None:
            _record_finish_reason(model_name, choice.finish_reason)
            if str(choice.finish_reason).lower() == "content_filter":
                raise LLMProviderError(f"Groq stream blocked by content_filter on {model_name}.")
        if choice.delta.content:
            yield choice.delta.content


def _stream_groq(
    messages: list[dict[str, str]],
    skip_primary: bool = False,
) -> Generator[str, None, None]:
    """Streams tokens from Groq API, guarding against cross-model partial concatenation."""
    from groq import APIError, APIStatusError, Groq, RateLimitError  # noqa: PLC0415

    client = Groq(api_key=settings.groq_api_key)
    models = _get_groq_models(settings.llm_model)
    if skip_primary and len(models) > 1:
        models = models[1:]
    last_err: Exception | None = None
    for attempt_msgs in (messages, _trim_oversized_payload(messages)):
        for model_name in models:
            yielded_any = False
            try:
                for token in _stream_single_groq_attempt(client, model_name, attempt_msgs):
                    yield token
                    yielded_any = True
                if yielded_any:
                    return
            except (APIError, Exception) as exc:
                if yielded_any:
                    raise
                last_err = exc
        if attempt_msgs == messages and not isinstance(last_err, (RateLimitError, APIStatusError)):
            break
    if last_err:
        if _is_production_env():
            raise LLMProviderError(f"Groq provider error: {type(last_err).__name__}") from last_err
        yield from _stream_mock(messages, fallback_notice="[AI provider busy; answering from retrieved company documentation]")


def _stream_openai(messages: list[dict[str, str]]) -> Generator[str, None, None]:
    """Streams tokens from OpenAI API enforcing max_tokens, finish_reason, and error safety."""
    from openai import OpenAI  # noqa: PLC0415

    client = OpenAI(api_key=settings.openai_api_key)
    try:
        stream = client.chat.completions.create(
            model=settings.llm_model,
            messages=messages,  # type: ignore[arg-type]
            temperature=settings.llm_temperature,
            max_tokens=_resolved_max_output_tokens(),
            stream=True,
        )
        for chunk in stream:
            choice = chunk.choices[0]
            if getattr(choice, "finish_reason", None) is not None:
                _record_finish_reason(settings.llm_model, choice.finish_reason)
                if str(choice.finish_reason).lower() == "content_filter":
                    raise LLMProviderError("OpenAI stream blocked by content_filter.")
            if choice.delta.content:
                yield choice.delta.content
    except LLMProviderError:
        raise
    except Exception as exc:
        if _is_production_env():
            raise LLMProviderError(f"OpenAI provider error: {type(exc).__name__}") from exc
        raise


def _extract_gemini_finish_reason(chunk: object) -> object:
    """Extracts the first candidate finish_reason from a Gemini stream chunk when present."""
    candidates = getattr(chunk, "candidates", None) or []
    if not candidates:
        return None
    return getattr(candidates[0], "finish_reason", None)


def _is_abnormal_gemini_finish(finish_reason: object) -> bool:
    """Returns True when Gemini terminated due to token limit, safety, or non-STOP reason."""
    if finish_reason is None:
        return False
    raw_val = getattr(finish_reason, "value", finish_reason)
    if isinstance(raw_val, int):
        return raw_val not in (0, 1)
    label = str(finish_reason).strip().upper()
    return label not in ("", "0", "1", "STOP", "FINISH_REASON_UNSPECIFIED", "FINISHREASON.STOP", "FINISHREASON.FINISH_REASON_UNSPECIFIED")


def _get_gemini_models(primary: str) -> list[str]:
    """Returns candidate Gemini models using verified active full-Flash models."""
    candidates = [primary, "gemini-3.5-flash", "gemini-3-flash-preview", "gemini-flash-latest"]
    seen: set[str] = set()
    result: list[str] = []
    for m in candidates:
        if m and m not in seen:
            seen.add(m)
            result.append(m)
    return result


def _build_gemini_chat_inputs(
    messages: list[dict[str, str]],
) -> tuple[str, list[dict[str, object]], str]:
    """Splits chat messages into Gemini system instruction, chat history, and latest prompt."""
    system_content = next((m["content"] for m in messages if m["role"] == "system"), "")
    non_system = [m for m in messages if m["role"] != "system"]
    gemini_history: list[dict[str, object]] = [
        {"role": "user" if m["role"] == "user" else "model", "parts": [m["content"]]}
        for m in non_system[:-1]
    ]
    last_user = non_system[-1]["content"] if non_system else ""
    return system_content, gemini_history, last_user


def _stream_gemini(
    messages: list[dict[str, str]],
    skip_primary: bool = False,
) -> Generator[str, None, None]:
    """Streams tokens from Gemini API while guarding against partial or truncated streams."""
    import google.generativeai as genai  # noqa: PLC0415

    genai.configure(api_key=settings.gemini_api_key, transport="rest")
    system_content, gemini_history, last_user = _build_gemini_chat_inputs(messages)
    models = _get_gemini_models(settings.llm_model)
    if skip_primary and len(models) > 1:
        models = models[1:]
    last_err: Exception | None = None
    for model_name in models:
        yielded, last_finish = False, None
        try:
            model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=system_content or None,
                generation_config={
                    "temperature": settings.llm_temperature,
                    "max_output_tokens": _resolved_max_output_tokens(),
                },
            )
            response = model.start_chat(history=gemini_history).send_message(last_user, stream=True)
            for chunk in response:
                fin = _extract_gemini_finish_reason(chunk)
                if fin is not None:
                    last_finish = fin
                    _record_finish_reason(model_name, fin)
                text = _extract_gemini_chunk_text(chunk)
                if text:
                    yield text
                    yielded = True
            if _is_abnormal_gemini_finish(last_finish):
                raise LLMProviderError(f"Gemini stream stopped early on {model_name}: finish_reason={last_finish}")
            if yielded:
                return
        except Exception as exc:
            if yielded:
                raise
            last_err = exc
    if last_err:
        raise last_err


def _complete_groq(
    messages: list[dict[str, str]],
    max_tokens: int,
    temperature: float,
    timeout: float,
    model: str | None = None,
) -> str:
    """Completes query rewrite via Groq API with fallback on rate limits."""
    from groq import APIError, APIStatusError, Groq, RateLimitError  # noqa: PLC0415

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
            content = resp.choices[0].message.content or ""
            if content.strip():
                return content.strip()
        except (APIError, Exception) as exc:
            last_err = exc
            continue
    if last_err:
        return ""
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
    """Completes query rewrite via Google Gemini generative API with model fallback."""
    import google.generativeai as genai  # noqa: PLC0415

    genai.configure(api_key=settings.gemini_api_key, transport="rest")
    target_model = model or settings.rewrite_model or settings.llm_model
    models = _get_gemini_models(target_model)
    system_content = next((m["content"] for m in messages if m["role"] == "system"), "")
    non_system = [m for m in messages if m["role"] != "system"]
    user_prompt = non_system[-1]["content"] if non_system else ""

    for model_name in models:
        try:
            gemini_model = genai.GenerativeModel(
                model_name=model_name,
                system_instruction=system_content if system_content else None,
                generation_config={"temperature": temperature, "max_output_tokens": max_tokens},
            )
            resp = gemini_model.generate_content(user_prompt, request_options={"timeout": timeout})
            if resp.text:
                return resp.text.strip()
        except Exception:
            continue
    return user_prompt


def _complete_mock(messages: list[dict[str, str]]) -> str:
    """Returns deterministic mock completion text."""
    user_msg = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
    return user_msg


