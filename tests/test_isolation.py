"""Tests verifying socket-level network isolation and absence of live external calls."""
from __future__ import annotations

import httpx
import pytest

from app.config import settings


def test_external_httpx_fails_at_socket_level() -> None:
    """Verifies that external network access via httpx is blocked at the socket level."""
    with pytest.raises(RuntimeError, match="Network access blocked in tests"):
        httpx.get("https://example.com", timeout=1.0)


def test_provider_api_keys_unset_in_tests() -> None:
    """Verifies that all provider API keys are stripped in the test environment."""
    assert settings.groq_api_key == ""
    assert settings.openai_api_key == ""
    assert settings.gemini_api_key == ""
    assert settings.llm_provider == "mock"
