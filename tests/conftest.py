"""Global pytest configuration and automated test environment fixtures."""
from __future__ import annotations

import os
import socket
from typing import Any, Generator

import pytest

# Ensure MOCK_TOKEN_DELAY_MS is zeroed out immediately for testing
os.environ["MOCK_TOKEN_DELAY_MS"] = "0"

from app.config import settings  # noqa: E402

settings.mock_token_delay_ms = 0

LLM_NETWORK_CALLS: list[str] = []
ALLOWED_TEST_HOSTS: set[str] = {"127.0.0.1", "localhost", "::1"}


@pytest.fixture(autouse=True)
def enforce_test_isolation(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> Generator[None, None, None]:
    """Forces mock mode, test defaults, isolated sync paths, and blocks external sockets."""
    LLM_NETWORK_CALLS.clear()
    sync_tmp = tmp_path_factory.mktemp("sync_state")
    monkeypatch.setenv("SYNC_CHECKPOINT_PATH", str(sync_tmp / "test_sync_checkpoints.json"))
    monkeypatch.setenv("SYNC_LOCK_PATH", str(sync_tmp / "test_sync.lock"))
    monkeypatch.setattr(settings, "mock_token_delay_ms", 0)
    monkeypatch.setattr(settings, "llm_provider", "mock")
    monkeypatch.setattr(settings, "chunking_mode", "section")
    monkeypatch.setattr(settings, "hybrid_search", False)
    monkeypatch.setattr(settings, "reranker", False)
    monkeypatch.setattr(settings, "grader", False)
    monkeypatch.setattr(settings, "query_rewrite", False)
    monkeypatch.setattr(settings, "rewrite_mode", "replace")
    monkeypatch.setattr(settings, "rewrite_model", "")
    monkeypatch.setattr(settings, "rewrite_backend", "llm")
    monkeypatch.setattr(settings, "rag_score_threshold", 0.80)

    # Unset all provider API keys in test environment
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(settings, "groq_api_key", "")
    monkeypatch.setattr(settings, "openai_api_key", "")
    monkeypatch.setattr(settings, "gemini_api_key", "")

    # Socket-level blocking for anything except localhost / 127.0.0.1
    orig_connect = socket.socket.connect
    orig_create = socket.create_connection

    def blocked_connect(self: socket.socket, address: Any) -> Any:
        host = address[0] if isinstance(address, (tuple, list)) else address
        if str(host) not in ALLOWED_TEST_HOSTS:
            LLM_NETWORK_CALLS.append(str(host))
            raise RuntimeError(f"Network access blocked in tests: {host}")
        return orig_connect(self, address)

    def blocked_create(address: Any, *args: Any, **kwargs: Any) -> Any:
        host = address[0] if isinstance(address, (tuple, list)) else address
        if str(host) not in ALLOWED_TEST_HOSTS:
            LLM_NETWORK_CALLS.append(str(host))
            raise RuntimeError(f"Network access blocked in tests: {host}")
        return orig_create(address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
    monkeypatch.setattr(socket, "create_connection", blocked_create)
    yield


def pytest_configure(config: pytest.Config) -> None:
    """Registers custom pytest markers for live external LLM test suites."""
    config.addinivalue_line(
        "markers", "requires_llm: marks tests requiring live external LLM API access"
    )
    config.addinivalue_line(
        "markers", "slow: marks slow tests requiring real ONNX reranker models"
    )

